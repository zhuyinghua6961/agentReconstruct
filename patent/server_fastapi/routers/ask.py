from __future__ import annotations

from functools import partial
import inspect
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

import anyio
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server.errors import codes
from server.errors.core import APIError
from server.patent.stream_events import (
    PATENT_STREAM_CAPABILITY_HEADER,
    inject_stream_capability_option,
)
from server.schemas.request_models import ProtocolMismatchRequestError, parse_patent_request
from server.services.ask_service import AskService
from server.runtime.request_context import get_trace_id
from server_fastapi.auth.deps import require_auth_context

router = APIRouter()
logger = logging.getLogger("patent.server_fastapi.ask")
_FILE_ROUTES = {"pdf_qa", "tabular_qa", "hybrid_qa"}



def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")



def _to_sse_line(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"



def _map_exception(exc: Exception) -> APIError:
    if isinstance(exc, APIError):
        return exc
    return APIError(
        code=codes.INTERNAL_ERROR,
        message="internal server error",
        status_code=500,
        error="internal_error",
        retriable=False,
    )



def _error_event(*, trace_id: str, seq: int, exc: Exception) -> dict[str, Any]:
    mapped = _map_exception(exc)
    return {
        "type": "error",
        "code": mapped.code,
        "error": mapped.error,
        "message": mapped.message,
        "trace_id": str(trace_id or ""),
        "seq": int(seq),
        "ts": _utc_iso(),
    }


def _is_truthy_header(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if not normalized:
        return False
    return normalized in {"1", "true", "yes", "on"}


async def _read_json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise APIError(
            code=codes.INVALID_REQUEST,
            message="request body must be valid JSON",
            status_code=400,
            error="invalid_request",
            retriable=False,
        ) from exc
    if not isinstance(payload, dict):
        raise APIError(
            code=codes.INVALID_REQUEST,
            message="request body must be a JSON object",
            status_code=400,
            error="invalid_request",
            retriable=False,
        )
    return dict(payload)


async def _parse_patent_request_or_raise(request: Request):
    payload = await _read_json_payload(request)
    gateway_task_execution = _is_truthy_header(request.headers.get("x-gateway-task-execution"))
    gateway_owned_persistence = _is_truthy_header(request.headers.get("x-gateway-owned-persistence"))
    patent_stream_capability = request.headers.get(PATENT_STREAM_CAPABILITY_HEADER)
    existing_options = payload.get("options")
    if isinstance(existing_options, dict):
        normalized_options = dict(existing_options)
        normalized_options.pop("gateway_task_execution", None)
        normalized_options.pop("gateway_owned_persistence", None)
        if gateway_task_execution:
            normalized_options["gateway_task_execution"] = True
        if gateway_owned_persistence:
            normalized_options["gateway_owned_persistence"] = True
        payload["options"] = inject_stream_capability_option(
            normalized_options,
            header_value=patent_stream_capability,
            route=payload.get("route"),
        )
    elif gateway_task_execution or gateway_owned_persistence:
        normalized_options: dict[str, Any] = {}
        if gateway_task_execution:
            normalized_options["gateway_task_execution"] = True
        if gateway_owned_persistence:
            normalized_options["gateway_owned_persistence"] = True
        payload["options"] = inject_stream_capability_option(
            normalized_options,
            header_value=patent_stream_capability,
            route=payload.get("route"),
        )
    elif patent_stream_capability is not None:
        payload["options"] = inject_stream_capability_option(
            None,
            header_value=patent_stream_capability,
            route=payload.get("route"),
        )
    try:
        return parse_patent_request(payload)
    except ProtocolMismatchRequestError as exc:
        raise APIError(
            code=codes.PROTOCOL_MISMATCH,
            message=str(exc),
            status_code=400,
            error="protocol_mismatch",
            retriable=False,
        ) from exc
    except ValueError as exc:
        raise APIError(
            code=codes.INVALID_REQUEST,
            message=str(exc),
            status_code=400,
            error="invalid_request",
            retriable=False,
        ) from exc



def _get_ask_service(request: Request) -> AskService:
    service = getattr(request.app.state, "ask_service", None)
    if service is None:
        raise APIError(
            code=codes.SERVICE_NOT_READY,
            message="patent ask service is not ready",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )
    return service


def _copy_components(request: Request) -> dict[str, Any]:
    source = getattr(request.app.state, "component_status", {})
    components = {name: dict(value or {}) for name, value in dict(source).items()}
    dispatcher = getattr(request.app.state, "runtime_dispatcher", None)
    if dispatcher is not None:
        runtime = dict(components.get("runtime") or {})
        dynamic_runtime = dict(dispatcher.runtime_state())
        runtime_ready = bool(runtime.get("ready", True)) and bool(dynamic_runtime.get("ready", True))
        runtime.update(dynamic_runtime)
        runtime["ready"] = runtime_ready
        components["runtime"] = runtime
    return components


def _ensure_durable_mode_enabled(*, request: Request, ask_request) -> None:
    if not ask_request.is_durable:
        return
    settings = getattr(request.app.state, "settings", None)
    if bool(getattr(settings, "durable_mode_enabled", False)):
        return
    raise APIError(
        code=codes.DURABLE_MODE_DISABLED,
        message="durable patent mode is disabled",
        status_code=503,
        error="durable_mode_disabled",
        retriable=False,
    )


def _ensure_patent_file_routes_enabled(*, request: Request, ask_request) -> None:
    if str(getattr(ask_request, "route", "") or "") not in _FILE_ROUTES:
        return
    settings = getattr(request.app.state, "settings", None)
    if bool(getattr(settings, "patent_file_routes_enabled", False)):
        return
    raise APIError(
        code=codes.PATENT_FILE_ROUTE_DISABLED,
        message="patent file routes are disabled",
        status_code=503,
        error="patent_file_route_disabled",
        retriable=False,
    )


def _ensure_durable_dependencies_ready(*, request: Request, ask_request) -> None:
    if not ask_request.is_durable:
        return
    components = _copy_components(request)
    required_components = ["redis", "authority"]
    if _request_requires_runtime(ask_request):
        required_components.append("runtime")
    ready = all(bool(dict(components.get(name) or {}).get("ready", False)) for name in required_components)
    if ready:
        return
    raise APIError(
        code=codes.SERVICE_NOT_READY,
        message="durable patent dependencies are not ready",
        status_code=503,
        error="service_not_ready",
        retriable=True,
        extra={"components": components},
    )


def _request_requires_runtime(ask_request) -> bool:
    route = str(getattr(ask_request, "route", "") or "")
    if route == "kb_qa":
        return True
    source_scope = str(getattr(ask_request, "source_scope", "") or "")
    return "kb" in source_scope.split("+")


def _resolve_user_id(*, ask_request, authorization: str | None) -> int | None:
    if not ask_request.is_durable:
        return None
    return require_auth_context(authorization).user_id



def _acquire_stream_slot(request: Request):
    dispatcher = getattr(request.app.state, "runtime_dispatcher", None)
    if dispatcher is None:
        return None
    lease = dispatcher.try_acquire_stream_slot()
    if lease is None:
        raise APIError(
            code=codes.PATENT_BUSY,
            message="too many running patent streams",
            status_code=429,
            error="patent_busy",
            retriable=True,
        )
    return lease


async def _run_in_ask_executor(request: Request, fn, /, *args, abandon_on_cancel: bool = False, **kwargs):
    dispatcher = getattr(request.app.state, "runtime_dispatcher", None)
    limiter = getattr(dispatcher, "ask_limiter", None)
    return await anyio.to_thread.run_sync(
        partial(fn, *args, **kwargs),
        limiter=limiter,
        abandon_on_cancel=abandon_on_cancel,
    )


def _close_stream(stream: Iterator[dict[str, Any]] | None) -> None:
    if stream is None:
        return
    close_stream = getattr(stream, "close", None)
    if callable(close_stream):
        close_stream()


def _open_stream_ask(service: AskService, ask_request, *, user_id: int | None, cancel_event: threading.Event):
    stream_ask = getattr(service, "stream_ask")
    try:
        signature = inspect.signature(stream_ask)
    except (TypeError, ValueError):
        return stream_ask(ask_request, user_id=user_id)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()) or "cancel_event" in signature.parameters:
        return stream_ask(ask_request, user_id=user_id, cancel_event=cancel_event)
    return stream_ask(ask_request, user_id=user_id)



def _build_streaming_response(*, request: Request, ask_request, user_id: int | None) -> StreamingResponse:
    service = _get_ask_service(request)
    lease = _acquire_stream_slot(request)
    trace_id = str(ask_request.trace_id)

    async def _generate() -> Iterator[str]:
        seq = 0
        current_trace_id = trace_id or get_trace_id()
        stream: Iterator[dict[str, Any]] | None = None
        stream_cancel_event = threading.Event()
        try:
            stream = await _run_in_ask_executor(
                request,
                _open_stream_ask,
                service,
                ask_request,
                user_id=user_id,
                cancel_event=stream_cancel_event,
            )
            while True:
                payload = await _run_in_ask_executor(
                    request,
                    _next_stream_payload,
                    stream,
                    abandon_on_cancel=True,
                )
                if payload is None:
                    break
                seq = int(payload.get("seq", seq))
                trace = str(payload.get("trace_id") or current_trace_id)
                if trace:
                    current_trace_id = trace
                trace_id_local = current_trace_id
                yield _to_sse_line({**dict(payload), "trace_id": trace_id_local})
                seq = int(payload.get("seq", seq)) + 1
        except Exception as exc:
            yield _to_sse_line(_error_event(trace_id=current_trace_id, seq=seq, exc=exc))
        finally:
            stream_cancel_event.set()
            try:
                _close_stream(stream)
            except Exception:
                logger.warning("patent ask_stream close failed trace_id=%s", current_trace_id, exc_info=True)
            if lease is not None:
                lease.release()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _next_stream_payload(stream: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(stream)
    except StopIteration:
        return None


@router.post("/api/ask")
@router.post("/api/v1/ask")
@router.post("/api/patent/ask")
@router.post("/api/v1/patent/ask")
async def patent_ask(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    logger.info("patent_ask received path=%s trace=%s", request.url.path, request.headers.get("x-trace-id") or request.headers.get("x-request-id"))
    ask_request = await _parse_patent_request_or_raise(request)
    _ensure_patent_file_routes_enabled(request=request, ask_request=ask_request)
    _ensure_durable_mode_enabled(request=request, ask_request=ask_request)
    user_id = _resolve_user_id(ask_request=ask_request, authorization=authorization)
    _ensure_durable_dependencies_ready(request=request, ask_request=ask_request)
    logger.info("patent_ask dispatching sync trace=%s durable=%s", ask_request.trace_id, ask_request.is_durable)
    payload = await _run_in_ask_executor(request, _get_ask_service(request).sync_ask, ask_request, user_id=user_id)
    logger.info("patent_ask completed sync trace=%s answer_chars=%s", ask_request.trace_id, len(str(payload.get("final_answer") or "")))
    return JSONResponse(content=payload)


@router.post("/api/ask_stream")
@router.post("/api/v1/ask_stream")
@router.post("/api/patent/ask_stream")
@router.post("/api/v1/patent/ask_stream")
async def patent_ask_stream(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    ask_request = await _parse_patent_request_or_raise(request)
    _ensure_patent_file_routes_enabled(request=request, ask_request=ask_request)
    _ensure_durable_mode_enabled(request=request, ask_request=ask_request)
    user_id = _resolve_user_id(ask_request=ask_request, authorization=authorization)
    _ensure_durable_dependencies_ready(request=request, ask_request=ask_request)
    return _build_streaming_response(request=request, ask_request=ask_request, user_id=user_id)
