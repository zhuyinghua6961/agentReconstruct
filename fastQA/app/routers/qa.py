from __future__ import annotations

import logging
from threading import Event
import uuid
from typing import Any, Iterator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.runtime import generation_runtime_is_ready
from app.core.sse import sse_response
from app.modules.qa_kb.models import QaKbRequest
from app.modules.qa_kb.service import qa_kb_service
from app.modules.qa_kb.streaming import build_reference_links, normalize_reference_objects, normalize_references
from app.services.file_routes import iter_pdf_route_events, iter_tabular_route_events, resolve_gateway_file_context
from app.services.request_adapter import GatewayAskRequest, RequestAdapterError, adapt_gateway_ask_payload
from app.services.stream_contract import AskStreamTap

router = APIRouter(tags=["qa"])
_ALLOWED_MODES = {"fast"}


class AskRequest(BaseModel):
    question: str = Field(default="", max_length=4000)
    conversation_id: int | str | None = None
    user_id: int | str | None = None
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    requested_mode: Literal["fast", "thinking", "patent"] = "fast"
    actual_mode: Literal["fast", "thinking", "patent"] | None = None
    route: Literal["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"] | None = None
    turn_mode: Literal["kb_only", "file_only", "mixed"] | None = None
    allow_kb_verification: bool = False
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    execution_files: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    route_hint: str | None = None
    use_pdf: bool = False
    current_pdf_path: str | None = None
    pdf_path: str | None = None
    pdf_context: dict[str, Any] = Field(default_factory=dict)
    use_generation_driven: bool = False
    n_results_per_claim: int | None = None
    active_stream_count: int | None = None


def _adapter_error_payload(*, exc: RequestAdapterError, trace_id: str, requested_mode: str, actual_mode: str, route: str) -> dict[str, Any]:
    return {
        "success": False,
        "code": str(exc.code or "FASTQA_REQUEST_INVALID").upper(),
        "error": str(exc.message or "fastQA 请求不兼容当前阶段"),
        "message": str(exc.message or "fastQA request is not compatible"),
        "trace_id": trace_id,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "route": route,
        "detail": dict(exc.detail or {}),
    }


def _trace_id(request: Request, payload: AskRequest) -> str:
    header_value = str(request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID") or "").strip()
    if header_value:
        return header_value
    payload_value = str(payload.trace_id or "").strip()
    if payload_value:
        return payload_value
    return uuid.uuid4().hex


def _request_logger(request: Request, trace_id: str, route: str = "-") -> logging.LoggerAdapter:
    base_logger = getattr(request.app, "logger", None) or logging.getLogger(__name__)
    return logging.LoggerAdapter(base_logger, {"trace_id": trace_id, "route": route or "-"})


def _requested_route(payload: AskRequest) -> str:
    return str(payload.route or payload.route_hint or "kb_qa").strip() or "kb_qa"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _conversation_id_int(value: Any) -> int | None:
    return _positive_int(value)


def _resolve_user_id(request: Request, payload: AskRequest) -> tuple[int | None, RequestAdapterError | None]:
    header_user_id = None
    for header_name in ("X-User-ID", "X-Auth-Request-User-Id", "X-Forwarded-User-Id"):
        candidate = _positive_int(request.headers.get(header_name))
        if candidate is not None:
            header_user_id = candidate
            break
    payload_user_id = _positive_int(payload.user_id)
    if header_user_id is not None and payload_user_id is not None and header_user_id != payload_user_id:
        return None, RequestAdapterError(
            code="user_id_mismatch",
            message="user_id in header and body are inconsistent",
            detail={"header_user_id": header_user_id, "payload_user_id": payload_user_id},
        )
    return header_user_id or payload_user_id, None


def _call_optional_hook(*, request: Request, hook_name: str, kwargs: dict[str, Any]) -> None:
    hook = getattr(request.app.state, hook_name, None)
    if not callable(hook):
        return
    logger = getattr(request.app, "logger", None)
    try:
        hook(**kwargs)
    except Exception:
        if logger is not None:
            logger.warning("fastqa optional hook failed: %s", hook_name, exc_info=True)


def _persist_user_message_if_needed(*, request: Request, adapted_request: GatewayAskRequest, route: str, trace_id: str) -> None:
    conversation_id = _conversation_id_int(adapted_request.conversation_id)
    if conversation_id is None:
        return
    _call_optional_hook(
        request=request,
        hook_name="persist_user_message_hook",
        kwargs={
            "user_id": adapted_request.user_id,
            "conversation_id": conversation_id,
            "question": adapted_request.question,
            "trace_id": trace_id,
            "route": route,
            "requested_mode": adapted_request.requested_mode,
            "actual_mode": adapted_request.actual_mode,
            "payload": adapted_request,
        },
    )


def _persist_assistant_summary_if_needed(
    *,
    request: Request,
    adapted_request: GatewayAskRequest,
    tap: AskStreamTap,
    route: str,
    trace_id: str,
) -> None:
    conversation_id = _conversation_id_int(adapted_request.conversation_id)
    if conversation_id is None:
        return
    summary = tap.summary
    if not summary.done_seen:
        return
    _call_optional_hook(
        request=request,
        hook_name="persist_assistant_summary_hook",
        kwargs={
            "user_id": adapted_request.user_id,
            "conversation_id": conversation_id,
            "trace_id": summary.trace_id or trace_id,
            "route": summary.route or route,
            "requested_mode": adapted_request.requested_mode,
            "actual_mode": adapted_request.actual_mode,
            "assistant_content": str(summary.assistant_content or ""),
            "summary": {
                "assistant_content": str(summary.assistant_content or ""),
                "query_mode": summary.query_mode or route,
                "references": list(summary.references or []),
                "reference_objects": list(summary.reference_objects or []),
                "steps": list(summary.steps or []),
                "route": summary.route or route,
                "used_files": list(summary.used_files or []),
                "timings": dict(summary.timings or {}),
                "trace_id": summary.trace_id or trace_id,
                "file_selection": dict(summary.file_selection or {}),
                "done_seen": bool(summary.done_seen),
            },
            "payload": adapted_request,
        },
    )


def _metadata_event(
    *,
    route: str,
    requested_mode: str,
    actual_mode: str,
    trace_id: str,
    query_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "metadata",
        "query_mode": str(query_mode or route),
        "route": route,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "trace_id": trace_id,
    }


def _done_event(
    *,
    route: str,
    used_files: list[dict[str, Any]],
    trace_id: str,
    timings: dict[str, Any] | None = None,
    references: list[Any] | None = None,
    file_selection: dict[str, Any] | None = None,
    query_mode: str | None = None,
) -> dict[str, Any]:
    normalized_reference_objects = normalize_reference_objects(list(references or []))
    normalized_references = normalize_references(normalized_reference_objects)
    links = build_reference_links(normalized_references)
    resolved_query_mode = str(query_mode or route)
    return {
        "type": "done",
        "references": normalized_references,
        "reference_objects": normalized_reference_objects,
        "reference_links": links,
        "pdf_links": links,
        "doi_locations": [],
        "route": route,
        "used_files": list(used_files or []),
        "timings": dict(timings or {}),
        "metadata": {"route": route, "query_mode": resolved_query_mode},
        "query_mode": resolved_query_mode,
        "trace_id": trace_id,
        "file_selection": dict(file_selection or {}),
    }


def _runtime_error_event(
    *,
    code: str,
    error: str,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "type": "error",
        "success": False,
        "code": code,
        "error": error,
        "message": error,
        "trace_id": trace_id,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "route": route,
    }
    if detail:
        payload["detail"] = dict(detail)
    return payload


def _iter_terminal_frames(metadata: dict[str, Any], error_payload: dict[str, Any], done_payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield metadata
    yield error_payload
    yield done_payload


def _event_query_mode(event: dict[str, Any], route: str) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return str(event.get("query_mode") or metadata.get("query_mode") or event.get("route") or route)


def _busy_payload(*, request: Request) -> tuple[dict[str, Any], int]:
    limiter = request.app.state.ask_limiter
    snap = limiter.snapshot()
    return (
        {
            "success": False,
            "code": "ASK_STREAM_BUSY",
            "error": "server_busy",
            "message": f"当前进行中的 fastQA 问答较多（上限 {snap.limit}），请稍后重试",
            "active": snap.active,
            "limit": snap.limit,
        },
        429,
    )


def _adapt_request(request: Request, payload: AskRequest, trace_id: str) -> tuple[GatewayAskRequest | None, dict[str, Any] | None]:
    raw = payload.model_dump()
    raw["trace_id"] = trace_id
    resolved_user_id, user_id_error = _resolve_user_id(request, payload)
    if user_id_error is not None:
        return None, _adapter_error_payload(
            exc=user_id_error,
            trace_id=trace_id,
            requested_mode=str(raw.get("requested_mode") or "fast").strip() or "fast",
            actual_mode=str(raw.get("actual_mode") or raw.get("requested_mode") or "fast").strip() or "fast",
            route=str(raw.get("route") or raw.get("route_hint") or "kb_qa").strip() or "kb_qa",
        )
    raw["user_id"] = resolved_user_id
    requested_mode = str(raw.get("requested_mode") or "fast").strip() or "fast"
    actual_mode = str(raw.get("actual_mode") or requested_mode or "fast").strip() or "fast"
    route = str(raw.get("route") or raw.get("route_hint") or "kb_qa").strip() or "kb_qa"
    try:
        adapted = adapt_gateway_ask_payload(raw)
    except RequestAdapterError as exc:
        return None, _adapter_error_payload(
            exc=exc,
            trace_id=trace_id,
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            route=route,
        )
    return adapted, None


def _resolve_route_context(adapted_request: GatewayAskRequest, request: Request) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    logger = request.app.logger if hasattr(request.app, "logger") else None
    file_context = resolve_gateway_file_context(adapted_request=adapted_request, logger=logger)
    route = adapted_request.route
    if file_context and not adapted_request.route_was_explicit:
        route = str(file_context.get("route_hint") or route)
    used_files = list((file_context or {}).get("used_files") or adapted_request.used_files or adapted_request.execution_files or [])
    file_selection = {}
    if file_context:
        file_selection = {
            "strategy": str(file_context.get("strategy") or ""),
            "selection_semantic": str(file_context.get("selection_semantic") or ""),
            "turn_mode": str(file_context.get("turn_mode") or adapted_request.turn_mode or "kb_only"),
            "allow_kb_verification": bool(file_context.get("allow_kb_verification", adapted_request.allow_kb_verification)),
            "selected_file_ids": list(file_context.get("selected_file_ids") or []),
        }
    return route, file_context, used_files, file_selection


def _iter_route_events(
    *,
    request: Request,
    adapted_request: GatewayAskRequest,
    route: str,
    file_context: dict[str, Any] | None,
    should_cancel,
) -> Iterator[dict[str, Any]]:
    logger = _request_logger(request, adapted_request.trace_id, route)
    logger.info(
        "fastqa route dispatch route=%s requested_mode=%s actual_mode=%s question_chars=%s n_results_per_claim=%s used_files=%s",
        route,
        adapted_request.requested_mode,
        adapted_request.actual_mode,
        len(str(adapted_request.question or "")),
        adapted_request.n_results_per_claim,
        len(list(adapted_request.used_files or adapted_request.execution_files or [])),
    )
    if route == "kb_qa":
        runtime = getattr(request.app.state, "generation_runtime", None) if generation_runtime_is_ready(request.app.state) else None
        redis_service = getattr(request.app.state, "redis_service", None)
        if runtime is None:
            logger.error("fastqa generation runtime unavailable for kb_qa route")
            yield _runtime_error_event(
                code="FASTQA_NOT_READY",
                error="fastQA generation runtime is not ready",
                trace_id=adapted_request.trace_id,
                route=route,
                requested_mode=adapted_request.requested_mode,
                actual_mode=adapted_request.actual_mode,
            )
            return
        limiter = getattr(request.app.state, "ask_limiter", None)
        server_active_stream_count = None
        if limiter is not None:
            server_active_stream_count = int(limiter.snapshot().active)
        qa_request = QaKbRequest(
            question=adapted_request.question,
            request_use_generation_driven=adapted_request.request_use_generation_driven,
            route_hint=route,
            n_results_per_claim=adapted_request.n_results_per_claim,
            active_stream_count=server_active_stream_count,
            trace_id=adapted_request.trace_id,
        )
        yield from qa_kb_service.iter_answer_events(
            request=qa_request,
            generation_runtime=runtime,
            redis_service=redis_service,
            sse_event=lambda event: event,
            should_cancel=should_cancel,
            logger=logger,
        )
        return
    if route == "pdf_qa":
        logger.info("fastqa dispatching to pdf_qa handler")
        yield from iter_pdf_route_events(
            app_state=request.app.state,
            adapted_request=adapted_request,
            file_context=file_context,
            sse_event=lambda event: event,
            is_cancelled=should_cancel,
        )
        return
    if route in {"tabular_qa", "hybrid_qa"}:
        logger.info("fastqa dispatching to %s handler", route)
        yield from iter_tabular_route_events(
            app_state=request.app.state,
            adapted_request=adapted_request,
            file_context=file_context,
            route=route,
            sse_event=lambda event: event,
            is_cancelled=should_cancel,
        )
        return
    logger.error("fastqa received unsupported route=%s", route)
    yield _runtime_error_event(
        code="FASTQA_ROUTE_INVALID",
        error=f"unsupported route: {route}",
        trace_id=adapted_request.trace_id,
        route=route,
        requested_mode=adapted_request.requested_mode,
        actual_mode=adapted_request.actual_mode,
    )


def _iter_qa_frames(*, request: Request, payload: AskRequest, adapted_request: GatewayAskRequest, limiter: Any, trace_id: str, cancel_event: Event) -> Iterator[dict[str, Any]]:
    route, file_context, used_files, file_selection = _resolve_route_context(adapted_request, request)
    requested_mode = adapted_request.requested_mode
    actual_mode = adapted_request.actual_mode or "fast"
    done_emitted = False
    metadata_emitted = False
    logger = _request_logger(request, trace_id, route)

    logger.info(
        "fastqa stream begin route=%s requested_mode=%s actual_mode=%s question_chars=%s conversation_id=%s user_id=%s used_files=%s",
        route,
        requested_mode,
        actual_mode,
        len(str(adapted_request.question or "")),
        adapted_request.conversation_id,
        adapted_request.user_id,
        len(used_files),
    )
    if file_context:
        logger.info(
            "fastqa file context strategy=%s turn_mode=%s allow_kb_verification=%s selected_file_ids=%s needs_clarification=%s",
            str(file_context.get("strategy") or ""),
            str(file_context.get("turn_mode") or adapted_request.turn_mode or "kb_only"),
            bool(file_context.get("allow_kb_verification", adapted_request.allow_kb_verification)),
            list(file_context.get("selected_file_ids") or []),
            bool(file_context.get("needs_clarification")),
        )

    try:
        if (file_context or {}).get("needs_clarification"):
            logger.warning("fastqa file selection requires clarification before execution")
            yield {
                "type": "metadata",
                "query_mode": "文件选择澄清",
                "requested_mode": requested_mode,
                "actual_mode": actual_mode,
                "route": route,
                "trace_id": trace_id,
            }
            yield {
                "type": "error",
                "error": str((file_context or {}).get("clarification_message") or "文件选择需要澄清"),
                "message": str((file_context or {}).get("clarification_message") or "文件选择需要澄清"),
                "requested_mode": requested_mode,
                "actual_mode": actual_mode,
                "route": route,
                "trace_id": trace_id,
            }
            yield _done_event(
                route=route,
                used_files=used_files,
                trace_id=trace_id,
                file_selection=file_selection,
                query_mode="文件选择澄清",
            )
            return
        for event in _iter_route_events(
            request=request,
            adapted_request=adapted_request,
            route=route,
            file_context=file_context,
            should_cancel=cancel_event.is_set,
        ):
            if cancel_event.is_set():
                return
            event_type = str(event.get("type") or "").strip().lower()
            if event_type == "done":
                done_event = dict(event)
                query_mode = _event_query_mode(done_event, route)
                if not metadata_emitted:
                    metadata_emitted = True
                    yield _metadata_event(
                        route=str(done_event.get("route") or route),
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        trace_id=trace_id,
                        query_mode=query_mode,
                    )
                raw_reference_objects = done_event.get("reference_objects")
                normalized_reference_objects = normalize_reference_objects(
                    raw_reference_objects if isinstance(raw_reference_objects, list) else done_event.get("references")
                )
                normalized_references = normalize_references(normalized_reference_objects)
                links = build_reference_links(normalized_references)
                done_event["references"] = normalized_references
                done_event["reference_objects"] = normalized_reference_objects
                done_event.setdefault("reference_links", links)
                done_event.setdefault("pdf_links", links)
                done_event.setdefault("doi_locations", [])
                done_event.setdefault("timings", {})
                done_event.setdefault("trace_id", trace_id)
                done_event.setdefault("query_mode", query_mode)
                done_event["used_files"] = list(done_event.get("used_files") or used_files)
                done_event["file_selection"] = dict(done_event.get("file_selection") or file_selection)
                done_event["metadata"] = {
                    **dict(done_event.get("metadata") or {}),
                    "requested_mode": requested_mode,
                    "actual_mode": actual_mode,
                    "route": str(done_event.get("route") or route),
                    "query_mode": query_mode,
                }
                logger.info(
                    "fastqa done event route=%s query_mode=%s refs=%s timings=%s content_chars=%s",
                    done_event.get("route") or route,
                    query_mode,
                    len(normalized_references),
                    done_event.get("timings") or {},
                    len(str(done_event.get("final_answer") or "")),
                )
                done_emitted = True
                yield done_event
                continue
            if event_type == "metadata":
                metadata_event = dict(event)
                metadata_event.setdefault("requested_mode", requested_mode)
                metadata_event.setdefault("actual_mode", actual_mode)
                metadata_event.setdefault("route", route)
                metadata_event.setdefault("trace_id", trace_id)
                metadata_emitted = True
                yield metadata_event
                continue
            if event_type == "error":
                error_event = dict(event)
                error_event.setdefault("trace_id", trace_id)
                error_event.setdefault("requested_mode", requested_mode)
                error_event.setdefault("actual_mode", actual_mode)
                error_event.setdefault("route", route)
                if not metadata_emitted:
                    metadata_emitted = True
                    yield _metadata_event(
                        route=route,
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        trace_id=trace_id,
                        query_mode=_event_query_mode(error_event, route),
                    )
                logger.warning(
                    "fastqa error event route=%s code=%s error=%s",
                    error_event.get("route") or route,
                    error_event.get("code") or "",
                    error_event.get("error") or error_event.get("message") or "",
                )
                yield error_event
                if not done_emitted:
                    done_emitted = True
                    yield _done_event(
                        route=route,
                        used_files=used_files,
                        trace_id=trace_id,
                        file_selection=file_selection,
                        query_mode=_event_query_mode(error_event, route),
                    )
                return
            yield event
        if not cancel_event.is_set() and not done_emitted:
            if not metadata_emitted:
                yield _metadata_event(
                    route=route,
                    requested_mode=requested_mode,
                    actual_mode=actual_mode,
                    trace_id=trace_id,
                    query_mode=route,
                )
            logger.info("fastqa stream finished without explicit done event; emitting synthetic done route=%s", route)
            yield _done_event(route=route, used_files=used_files, trace_id=trace_id, file_selection=file_selection, query_mode=route)
    except Exception as exc:
        logger.error("fastqa stream execution failed route=%s error=%s", route, exc, exc_info=True)
        if not metadata_emitted:
            yield _metadata_event(
                route=route,
                requested_mode=requested_mode,
                actual_mode=actual_mode,
                trace_id=trace_id,
                query_mode=route,
            )
        yield _runtime_error_event(
            code="FASTQA_RUNTIME_ERROR",
            error=f"fastQA 执行异常: {exc}",
            trace_id=trace_id,
            route=route,
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            detail={"exception_type": exc.__class__.__name__},
        )
        yield _done_event(route=route, used_files=used_files, trace_id=trace_id, file_selection=file_selection, query_mode=route)
    finally:
        limiter.release()


def _log_stream_summary(*, request: Request, tap: AskStreamTap, trace_id: str, route: str) -> None:
    summary = tap.summary
    logger = _request_logger(request, summary.trace_id or trace_id, summary.route or route)
    logger.info(
        "fastqa stream summary route=%s query_mode=%s done_seen=%s content_chars=%s refs=%s ref_objects=%s steps=%s timings=%s file_selection=%s",
        summary.route or route,
        summary.query_mode or route,
        bool(summary.done_seen),
        len(str(summary.assistant_content or "")),
        len(summary.references or []),
        len(summary.reference_objects or []),
        len(summary.steps or []),
        dict(summary.timings or {}),
        dict(summary.file_selection or {}),
    )


def _wrap_stream_with_tap(
    *,
    request: Request,
    adapted_request: GatewayAskRequest,
    route: str,
    trace_id: str,
    source: Iterator[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    tap = AskStreamTap()

    def _iter() -> Iterator[dict[str, Any]]:
        try:
            yield from tap.wrap(source)
        finally:
            _log_stream_summary(request=request, tap=tap, trace_id=trace_id, route=route)
            _persist_assistant_summary_if_needed(
                request=request,
                adapted_request=adapted_request,
                tap=tap,
                route=route,
                trace_id=trace_id,
            )

    return _iter()


def _collect_sync_result(events: list[dict[str, Any]], *, trace_id: str, requested_mode: str, actual_mode: str, route: str, used_files: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    contents: list[str] = []
    references: list[str] = []
    reference_objects: list[dict[str, Any]] = []
    timings: dict[str, Any] = {}
    metadata: dict[str, Any] = {"requested_mode": requested_mode, "actual_mode": actual_mode, "route": route, "query_mode": route}
    error_payload: dict[str, Any] | None = None
    file_selection: dict[str, Any] = {}
    for event in events:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "content":
            contents.append(str(event.get("content") or ""))
        elif event_type == "done":
            reference_objects = normalize_reference_objects(
                event.get("reference_objects") if isinstance(event.get("reference_objects"), list) else event.get("references")
            )
            references = normalize_references(reference_objects)
            timings = dict(event.get("timings") or {})
            metadata = {**metadata, **dict(event.get("metadata") or {})}
            file_selection = dict(event.get("file_selection") or {})
        elif event_type == "error" and error_payload is None:
            error_payload = dict(event)
    links = build_reference_links(references)
    payload = {
        "success": error_payload is None,
        "final_answer": "".join(contents),
        "query_mode": metadata.get("query_mode") or route,
        "route": metadata.get("route") or route,
        "timings": timings,
        "references": references,
        "reference_objects": reference_objects,
        "reference_links": links,
        "pdf_links": links,
        "doi_locations": [],
        "metadata": metadata,
        "trace_id": trace_id,
        "used_files": used_files,
        "file_selection": file_selection,
    }
    if error_payload is not None:
        payload.update({
            "success": False,
            "error": error_payload.get("error"),
            "message": error_payload.get("message") or error_payload.get("error"),
            "code": error_payload.get("code") or "FASTQA_RUNTIME_ERROR",
        })
        return payload, 500
    return payload, 200


@router.post("/api/ask")
@router.post("/api/fast/ask")
def ask(payload: AskRequest, request: Request):
    trace_id = _trace_id(request, payload)
    adapted_request, adapter_error = _adapt_request(request, payload, trace_id)
    if adapter_error is not None:
        return JSONResponse(status_code=400, content=adapter_error)
    limiter = request.app.state.ask_limiter
    if not limiter.try_acquire():
        error_payload, status_code = _busy_payload(request=request)
        return JSONResponse(status_code=status_code, content=error_payload)
    cancel_event = Event()
    route, file_context, used_files, _file_selection = _resolve_route_context(adapted_request, request)
    _persist_user_message_if_needed(request=request, adapted_request=adapted_request, route=route, trace_id=trace_id)
    try:
        events = list(
            _wrap_stream_with_tap(
                request=request,
                adapted_request=adapted_request,
                route=route,
                trace_id=trace_id,
                source=_iter_route_frames(
                    request=request,
                    payload=payload,
                    adapted_request=adapted_request,
                    limiter=limiter,
                    trace_id=trace_id,
                    cancel_event=cancel_event,
                ),
            )
        )
    finally:
        limiter.release()
    response_payload, status_code = _collect_sync_result(
        events,
        trace_id=trace_id,
        requested_mode=adapted_request.requested_mode,
        actual_mode=adapted_request.actual_mode,
        route=route,
        used_files=used_files,
    )
    return JSONResponse(status_code=status_code, content=response_payload)


@router.post("/api/ask_stream")
@router.post("/api/v1/ask")
@router.post("/api/v1/ask_stream")
@router.post("/api/fast/ask_stream")
@router.post("/api/v1/fast/ask")
@router.post("/api/v1/fast/ask_stream")
def ask_stream(payload: AskRequest, request: Request):
    trace_id = _trace_id(request, payload)
    adapted_request, adapter_error = _adapt_request(request, payload, trace_id)
    route = adapted_request.route if adapted_request is not None else _requested_route(payload)
    limiter = request.app.state.ask_limiter
    cancel_event = Event()
    if adapter_error is not None:
        return JSONResponse(status_code=400, content=adapter_error)
    if not limiter.try_acquire():
        error_payload, status_code = _busy_payload(request=request)
        return JSONResponse(status_code=status_code, content=error_payload)
    _persist_user_message_if_needed(request=request, adapted_request=adapted_request, route=route, trace_id=trace_id)
    return sse_response(
        request=request,
        source=_wrap_stream_with_tap(
            request=request,
            adapted_request=adapted_request,
            route=route,
            trace_id=trace_id,
            source=_iter_qa_frames(
                request=request,
                payload=payload,
                adapted_request=adapted_request,
                limiter=limiter,
                trace_id=trace_id,
                cancel_event=cancel_event,
            ),
        ),
        heartbeat_sec=request.app.state.settings.sse_heartbeat_sec,
        on_disconnect=lambda: (cancel_event.set(), limiter.release()),
    )


@router.post("/api/{mode}/ask")
def ask_mode(mode: str, payload: AskRequest, request: Request):
    if mode not in _ALLOWED_MODES:
        return JSONResponse(status_code=400, content={"success": False, "error": "mode_not_supported"})
    return ask(payload=payload, request=request)


@router.post("/api/v1/{mode}/ask")
@router.post("/api/{mode}/ask_stream")
@router.post("/api/v1/{mode}/ask_stream")
def ask_stream_mode(mode: str, payload: AskRequest, request: Request):
    if mode not in _ALLOWED_MODES:
        return JSONResponse(status_code=400, content={"success": False, "error": "mode_not_supported"})
    return ask_stream(payload=payload, request=request)


_iter_route_frames = _iter_qa_frames
