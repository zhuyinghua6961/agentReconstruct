"""FastAPI ask/ask_stream routes."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import dataclass
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterator

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
    AskCancelledError,
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


@dataclass(frozen=True)
class _SyncStreamItem:
    kind: str
    payload: Any = None


_SYNC_STREAM_EVENT = "event"
_SYNC_STREAM_DONE = "done"
_SYNC_STREAM_ERROR = "error"


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
    if isinstance(exc, AskCancelledError):
        return APIError(
            code="ASK_CANCELLED",
            message="cancelled",
            status_code=499,
            error="cancelled",
            retriable=False,
        )
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


def _header_truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _request_header(request: Request, header_name: str):
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        return getter(header_name)
    return None


def _gateway_owned_persistence(request: Request) -> bool:
    expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
    if not expected_token:
        return False
    internal_service_name = str(_request_header(request, "X-Internal-Service-Name") or "").strip().lower()
    internal_service_token = str(_request_header(request, "X-Internal-Service-Token") or "").strip()
    return (
        _header_truthy(_request_header(request, "X-Gateway-Task-Execution"))
        and _header_truthy(_request_header(request, "X-Gateway-Owned-Persistence"))
        and internal_service_name == "gateway"
        and internal_service_token == expected_token
    )


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
    if _gateway_owned_persistence(request):
        return
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
    if _gateway_owned_persistence(request):
        return
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


def _summary_payload(*, summary: dict, route: str, trace_id: str) -> dict:
    safe_summary = dict(summary or {})
    return {
        "assistant_content": str(safe_summary.get("assistant_content") or "").strip(),
        "query_mode": str(safe_summary.get("query_mode") or route or "").strip(),
        "references": list(safe_summary.get("references") or []),
        "reference_objects": list(safe_summary.get("reference_objects") or []),
        "reference_links": list(safe_summary.get("reference_links") or []),
        "pdf_links": list(safe_summary.get("pdf_links") or []),
        "doi_locations": dict(safe_summary.get("doi_locations") or {}),
        "steps": list(safe_summary.get("steps") or []),
        "route": str(safe_summary.get("route") or route or "").strip(),
        "used_files": list(safe_summary.get("used_files") or []),
        "timings": dict(safe_summary.get("timings") or {}),
        "trace_id": str(safe_summary.get("trace_id") or trace_id or "").strip(),
        "file_selection": dict(safe_summary.get("file_selection") or {}),
        "done_seen": bool(safe_summary.get("done_seen")),
    }


def _is_cancel_error(error_payload: dict) -> bool:
    code = str(error_payload.get("code") or "").strip().upper()
    error_text = str(error_payload.get("error") or error_payload.get("message") or "").strip().lower()
    return code in {"ASK_CANCELLED", "CLIENT_CANCELLED"} or error_text == "cancelled"


def _failure_from_error_payload(*, error_payload: dict, terminal_status: str) -> dict:
    detail = error_payload.get("detail") if isinstance(error_payload.get("detail"), dict) else {}
    failure_stage = str(error_payload.get("failure_stage") or detail.get("failure_stage") or "").strip()
    if not failure_stage:
        failure_stage = "cancelled" if terminal_status == "canceled" else "unknown"
    failure_code = str(error_payload.get("code") or detail.get("failure_code") or "").strip()
    failure_message = str(error_payload.get("message") or error_payload.get("error") or "").strip()
    if not failure_message:
        failure_message = "已取消" if terminal_status == "canceled" else "处理失败"
    retriable_raw = error_payload.get("retriable")
    if retriable_raw is None:
        retriable_raw = detail.get("retriable")
    retriable = False if terminal_status == "canceled" else bool(retriable_raw if retriable_raw is not None else True)
    return {
        "stage": failure_stage,
        "code": failure_code,
        "message": failure_message,
        "retriable": retriable,
    }


def _mapped_error_payload(*, exc: Exception, trace_id: str) -> tuple[APIError, dict]:
    mapped = _handle_service_error(exc)
    return mapped, {
        "code": mapped.code,
        "error": mapped.error,
        "message": mapped.message,
        "retriable": mapped.retriable,
        "trace_id": trace_id,
    }


def _persist_assistant_terminal_if_needed(
    *,
    request: Request,
    ask_request,
    summary: dict,
    terminal_status: str,
    error_payload: dict | None = None,
) -> None:
    if _gateway_owned_persistence(request):
        return
    if not _chat_persist_enabled(request):
        return
    user_id = int(ask_request.user_id) if ask_request.user_id else None
    conversation_id = _conversation_id_int(ask_request.conversation_id)
    if not user_id or not conversation_id:
        return
    summary_payload = _summary_payload(
        summary=summary,
        route=str(getattr(ask_request, "route", "thinking_qa") or "thinking_qa"),
        trace_id=str(getattr(ask_request, "trace_id", "") or ""),
    )
    try:
        chat_persistence.persist_assistant_terminal(
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=str(summary_payload.get("trace_id") or ""),
            route=str(summary_payload.get("route") or ""),
            requested_mode=str(getattr(ask_request, "requested_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
            actual_mode=str(getattr(ask_request, "actual_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
            terminal_status=terminal_status,
            assistant_content=str(summary_payload.get("assistant_content") or ""),
            summary=summary_payload,
            failure=None if terminal_status == "done" else _failure_from_error_payload(
                error_payload=error_payload or {},
                terminal_status=terminal_status,
            ),
            async_enabled=False,
        )
    except Exception as exc:  # pragma: no cover - defensive isolation
        request.app.logger.warning("assistant terminal persistence failed: %s", exc, exc_info=True)


def _start_sync_stream_producer(*, iterator: Iterator[dict], queue: asyncio.Queue[_SyncStreamItem], loop: asyncio.AbstractEventLoop, stop_event: threading.Event) -> threading.Thread:
    def _publish(item: _SyncStreamItem) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _run() -> None:
        try:
            for item in iterator:
                if stop_event.is_set():
                    break
                _publish(_SyncStreamItem(kind=_SYNC_STREAM_EVENT, payload=item))
        except Exception as exc:  # pragma: no cover - defensive isolation
            _publish(_SyncStreamItem(kind=_SYNC_STREAM_ERROR, payload=exc))
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            _publish(_SyncStreamItem(kind=_SYNC_STREAM_DONE))

    thread = threading.Thread(target=_run, daemon=True, name="thinkingqa-stream-producer")
    thread.start()
    return thread


async def _monitor_request_disconnect(*, request: Request, cancel_event: threading.Event, stop_event: threading.Event) -> None:
    try:
        while not stop_event.is_set():
            if await _request_disconnected(request):
                cancel_event.set()
                return
            await asyncio.sleep(0.05)
    except Exception:  # pragma: no cover - defensive isolation
        return


async def _request_disconnected(request: Request) -> bool:
    try:
        return bool(await request.is_disconnected())
    except Exception:  # pragma: no cover - defensive isolation
        return False


async def _execute_sync_ask_with_disconnect_support(*, request: Request, ask_request, trace_id: str) -> dict:
    if not _gateway_owned_persistence(request):
        return execute_ask(
            request=ask_request,
            timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
            trace_id=trace_id,
        )
    cancel_event = threading.Event()
    stop_event = threading.Event()
    monitor_task = asyncio.create_task(
        _monitor_request_disconnect(request=request, cancel_event=cancel_event, stop_event=stop_event)
    )
    try:
        return await asyncio.to_thread(
            execute_ask,
            request=ask_request,
            timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
            trace_id=trace_id,
            cancel_event=cancel_event,
        )
    finally:
        stop_event.set()
        await monitor_task


def _build_stream_response(*, request: Request, ask_request, trace_id: str, slot) -> StreamingResponse:
    gateway_task_mode = _gateway_owned_persistence(request)
    cancel_event = threading.Event()

    async def _generate():
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
            if cancel_event.is_set():
                return
            if not bool(summary.get("done_seen")):
                return
            content = str(summary.get("assistant_content") or "").strip()
            if not content:
                return
            _persist_assistant_message_if_needed(request=request, ask_request=ask_request, summary=dict(summary))
            assistant_persisted = True

        def _persist_terminal_once(*, terminal_status: str, error_payload: dict | None = None) -> None:
            nonlocal assistant_persisted
            if assistant_persisted:
                return
            _persist_assistant_terminal_if_needed(
                request=request,
                ask_request=ask_request,
                summary=dict(summary),
                terminal_status=terminal_status,
                error_payload=error_payload,
            )
            assistant_persisted = True
        def _completion_callback(payload: dict) -> None:
            if cancel_event.is_set():
                return
            with summary_lock:
                _ingest_done_payload(payload)
                _persist_summary_once()

        source = iter(
            stream_ask_events(
                request=ask_request,
                timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
                heartbeat_seconds=int(request.app.state.config["SSE_HEARTBEAT_SECONDS"]),
                trace_id=trace_id,
                completion_callback=_completion_callback,
                cancel_event=cancel_event,
            )
        )
        queue: asyncio.Queue[_SyncStreamItem] = asyncio.Queue()
        stop_event = threading.Event()
        monitor_task = asyncio.create_task(
            _monitor_request_disconnect(request=request, cancel_event=cancel_event, stop_event=stop_event)
        )
        producer = _start_sync_stream_producer(iterator=source, queue=queue, loop=asyncio.get_running_loop(), stop_event=stop_event)
        disconnected = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    if cancel_event.is_set():
                        disconnected = True
                        stop_event.set()
                        break
                    continue
                if item.kind == _SYNC_STREAM_DONE:
                    break
                if item.kind == _SYNC_STREAM_ERROR:
                    raise item.payload
                payload = dict(item.payload or {})
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
                elif event_type == "error":
                    with summary_lock:
                        terminal_status = "canceled" if _is_cancel_error(payload) else "failed"
                        _persist_terminal_once(terminal_status=terminal_status, error_payload=payload)
                seq += 1
                yield _to_sse_line(payload, seq=seq)
        except Exception as exc:  # pragma: no cover - defensive
            mapped, error_payload = _mapped_error_payload(exc=exc, trace_id=trace_id)
            with summary_lock:
                terminal_status = "canceled" if _is_cancel_error(error_payload) else "failed"
                _persist_terminal_once(terminal_status=terminal_status, error_payload=error_payload)
            yield _to_sse_line(
                {"type": "error", **error_payload},
                seq=seq + 1,
            )
        finally:
            stop_event.set()
            if disconnected:
                cancel_event.set()
            try:
                if not disconnected:
                    with summary_lock:
                        _persist_summary_once()
            except Exception as exc:  # pragma: no cover
                request.app.logger.warning("assistant persistence hook failed: %s", exc)
            await asyncio.to_thread(producer.join, 0.5)
            if not monitor_task.done():
                monitor_task.cancel()
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
            data = await _execute_sync_ask_with_disconnect_support(
                request=request,
                ask_request=ask_request,
                trace_id=trace_id,
            )
        except Exception as exc:  # pragma: no cover - transport-level mapping
            mapped, error_payload = _mapped_error_payload(exc=exc, trace_id=trace_id)
            _persist_assistant_terminal_if_needed(
                request=request,
                ask_request=ask_request,
                summary={
                    "assistant_content": "",
                    "route": str(getattr(ask_request, "route", "thinking_qa") or "thinking_qa"),
                    "trace_id": trace_id,
                    "done_seen": False,
                },
                terminal_status="canceled" if _is_cancel_error(error_payload) else "failed",
                error_payload=error_payload,
            )
            raise mapped
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
            data = await _execute_sync_ask_with_disconnect_support(
                request=request,
                ask_request=ask_request,
                trace_id=trace_id,
            )
        except Exception as exc:  # pragma: no cover - transport-level mapping
            mapped, error_payload = _mapped_error_payload(exc=exc, trace_id=trace_id)
            _persist_assistant_terminal_if_needed(
                request=request,
                ask_request=ask_request,
                summary={
                    "assistant_content": "",
                    "route": str(getattr(ask_request, "route", "thinking_qa") or "thinking_qa"),
                    "trace_id": trace_id,
                    "done_seen": False,
                },
                terminal_status="canceled" if _is_cancel_error(error_payload) else "failed",
                error_payload=error_payload,
            )
            raise mapped
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
