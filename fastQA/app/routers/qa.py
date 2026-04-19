from __future__ import annotations

import logging
import os
from dataclasses import replace
from threading import Event
import uuid
from typing import Any, Iterator, Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.runtime import generation_runtime_is_ready
from app.core.sse import sse_response
from app.modules.graph_kb.models import GraphKbExecutionResult, GraphRagPayload
from app.modules.graph_kb.service import route_graph_kb_v2, try_graph_kb_answer
from app.modules.qa_kb.models import QaKbRequest
from app.modules.qa_kb.service import qa_kb_service
from app.modules.qa_kb.streaming import build_doi_locations, normalize_reference_objects, normalize_references
from app.modules.storage.service import storage_service
from app.services.file_routes import iter_pdf_route_events, iter_tabular_route_events
from app.services.conversation_context_builder import build_conversation_context
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
    source_scope: str | None = None
    kb_enabled: bool = False
    turn_mode: Literal["kb_only", "file_only", "mixed"] | None = None
    allow_kb_verification: bool = False
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    execution_files: list[dict[str, Any]] = Field(default_factory=list)
    selected_file_ids: list[int | str] = Field(default_factory=list)
    primary_file_id: int | str | None = None
    file_selection: dict[str, Any] = Field(default_factory=dict)
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


class _CompatLoggerAdapter:
    def __init__(self, base_logger: Any, trace_id: str, route: str) -> None:
        self._base_logger = base_logger
        self._trace_id = trace_id
        self._route = route or "-"

    def _emit(self, method: str, message: str, *args: Any, **kwargs: Any) -> None:
        target = getattr(self._base_logger, method, None) or getattr(self._base_logger, "info", None)
        if not callable(target):
            return
        target(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._emit("info", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._emit("warning", message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._emit("error", message, *args, **kwargs)


def _request_logger(request: Request, trace_id: str, route: str = "-") -> Any:
    base_logger = getattr(request.app, "logger", None) or logging.getLogger(__name__)
    if hasattr(base_logger, "isEnabledFor") and hasattr(base_logger, "log"):
        return logging.LoggerAdapter(base_logger, {"trace_id": trace_id, "route": route or "-"})
    return _CompatLoggerAdapter(base_logger, trace_id, route)


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


def _authority_target(request: Request, attribute_name: str) -> str:
    settings = getattr(request.app.state, "settings", None)
    return str(getattr(settings, attribute_name, "") or "").strip().lower()


def _require_authority_user_write(request: Request) -> bool:
    return _authority_target(request, "conversation_execution_user_write_target") == "public_service"


def _require_authority_context_read(request: Request) -> bool:
    return _authority_target(request, "conversation_execution_context_read_target") == "public_service"


def _call_hook(*, request: Request, hook_name: str, kwargs: dict[str, Any], strict: bool = False) -> Any:
    hook = getattr(request.app.state, hook_name, None)
    if not callable(hook):
        if strict:
            raise RuntimeError(f"fastqa required hook missing: {hook_name}")
        return None
    logger = getattr(request.app, "logger", None)
    try:
        return hook(**kwargs)
    except Exception:
        if strict:
            raise
        if logger is not None:
            logger.warning("fastqa optional hook failed: %s", hook_name, exc_info=True)
        return None


def _hook_exists(*, request: Request, hook_name: str) -> bool:
    return callable(getattr(request.app.state, hook_name, None))


def _header_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _request_header(request: Request, header_name: str) -> Any:
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


def _persist_user_message_if_needed(*, request: Request, adapted_request: GatewayAskRequest, route: str, trace_id: str) -> None:
    if _gateway_owned_persistence(request):
        return
    conversation_id = _conversation_id_int(adapted_request.conversation_id)
    if conversation_id is None:
        return
    strict = _require_authority_user_write(request)
    if strict and _positive_int(adapted_request.user_id) is None:
        raise RuntimeError("fastqa authority user write requires user_id")
    result = _call_hook(
        request=request,
        hook_name="persist_user_message_hook",
        strict=strict,
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
    if strict and not isinstance(result, dict):
        raise RuntimeError("fastqa authority user write returned no result")


def _apply_authority_context(
    *,
    adapted_request: GatewayAskRequest,
    authority_context: dict[str, Any] | None,
) -> GatewayAskRequest:
    if not isinstance(authority_context, dict):
        return adapted_request
    chat_history = authority_context.get("chat_history")
    normalized_chat_history = [dict(item) for item in chat_history if isinstance(item, dict)] if isinstance(chat_history, list) else list(adapted_request.authority_chat_history)
    merged_options = dict(adapted_request.options or {})
    conversation_state = authority_context.get("conversation_state")
    if isinstance(conversation_state, dict):
        merged_options["authority_conversation_state"] = conversation_state
    summary = authority_context.get("summary")
    if isinstance(summary, dict):
        merged_options["authority_summary"] = summary
    pending_overlay = authority_context.get("pending_overlay")
    if isinstance(pending_overlay, dict):
        merged_options["authority_pending_overlay"] = dict(pending_overlay)
    if authority_context.get("snapshot_version") is not None:
        merged_options["authority_snapshot_version"] = authority_context.get("snapshot_version")
    return replace(
        adapted_request,
        chat_history=normalized_chat_history,
        authority_chat_history=normalized_chat_history,
        authority_conversation_state=dict(conversation_state or {}),
        authority_summary=dict(summary or {}),
        options=merged_options,
    )


def _load_conversation_context_if_needed(
    *,
    request: Request,
    adapted_request: GatewayAskRequest,
    route: str,
    trace_id: str,
) -> GatewayAskRequest:
    conversation_id = _conversation_id_int(adapted_request.conversation_id)
    if conversation_id is None:
        return adapted_request
    strict = _require_authority_context_read(request)
    if strict and _positive_int(adapted_request.user_id) is None:
        raise RuntimeError("fastqa authority context read requires user_id")
    authority_context = _call_hook(
        request=request,
        hook_name="load_conversation_context_hook",
        strict=strict,
        kwargs={
            "user_id": adapted_request.user_id,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
            "route": route,
            "requested_mode": adapted_request.requested_mode,
            "actual_mode": adapted_request.actual_mode,
            "payload": adapted_request,
        },
    )
    if strict and not isinstance(authority_context, dict):
        raise RuntimeError("fastqa authority context read returned no snapshot")
    return _apply_authority_context(adapted_request=adapted_request, authority_context=authority_context)


def _terminal_summary_payload(*, tap: AskStreamTap, route: str, trace_id: str) -> dict[str, Any]:
    summary = tap.summary
    return {
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
        "source_scope": str(summary.source_scope or ""),
        "source_usage": dict(summary.source_usage or {}),
        "done_seen": bool(summary.done_seen),
    }


def _is_cancel_error(error_payload: dict[str, Any]) -> bool:
    code = str(error_payload.get("code") or "").strip().upper()
    error_text = str(error_payload.get("error") or error_payload.get("message") or "").strip().lower()
    return code in {"ASK_CANCELLED", "FASTQA_CANCELLED", "CLIENT_CANCELLED"} or error_text == "cancelled"


def _failure_from_error_payload(*, error_payload: dict[str, Any], terminal_status: str) -> dict[str, Any]:
    detail = error_payload.get("detail") if isinstance(error_payload.get("detail"), dict) else {}
    failure_stage = (
        str(error_payload.get("failure_stage") or detail.get("failure_stage") or "").strip()
        or ("cancelled" if terminal_status == "canceled" else "unknown")
    )
    failure_code = str(error_payload.get("code") or detail.get("failure_code") or "").strip()
    failure_message = str(error_payload.get("message") or error_payload.get("error") or "").strip() or (
        "已取消" if terminal_status == "canceled" else "处理失败"
    )
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


def _persist_assistant_terminal_if_needed(
    *,
    request: Request,
    adapted_request: GatewayAskRequest,
    tap: AskStreamTap,
    route: str,
    trace_id: str,
    terminal_status: str,
    error_payload: dict[str, Any] | None = None,
) -> None:
    if _gateway_owned_persistence(request):
        return
    conversation_id = _conversation_id_int(adapted_request.conversation_id)
    if conversation_id is None:
        return
    summary_payload = _terminal_summary_payload(tap=tap, route=route, trace_id=trace_id)
    assistant_content = str(summary_payload.get("assistant_content") or "")
    failure = None if terminal_status == "done" else _failure_from_error_payload(
        error_payload=error_payload or {},
        terminal_status=terminal_status,
    )
    hook_kwargs = {
        "user_id": adapted_request.user_id,
        "conversation_id": conversation_id,
        "trace_id": str(summary_payload.get("trace_id") or trace_id),
        "route": str(summary_payload.get("route") or route),
        "requested_mode": adapted_request.requested_mode,
        "actual_mode": adapted_request.actual_mode,
        "terminal_status": terminal_status,
        "assistant_content": assistant_content,
        "summary": summary_payload,
        "failure": failure,
        "payload": adapted_request,
    }
    if _hook_exists(request=request, hook_name="persist_assistant_terminal_hook"):
        _call_hook(
            request=request,
            hook_name="persist_assistant_terminal_hook",
            kwargs=hook_kwargs,
        )
    elif terminal_status == "done":
        _call_hook(
            request=request,
            hook_name="persist_assistant_summary_hook",
            kwargs={
                "user_id": adapted_request.user_id,
                "conversation_id": conversation_id,
                "trace_id": str(summary_payload.get("trace_id") or trace_id),
                "route": str(summary_payload.get("route") or route),
                "requested_mode": adapted_request.requested_mode,
                "actual_mode": adapted_request.actual_mode,
                "assistant_content": assistant_content,
                "summary": summary_payload,
                "payload": adapted_request,
            },
        )


def _source_usage_from_scope(source_scope: str | None) -> dict[str, bool]:
    tokens = {part.strip().lower() for part in str(source_scope or "").split("+") if part.strip()}
    return {
        "pdf_used": "pdf" in tokens,
        "table_used": "table" in tokens,
        "kb_used": "kb" in tokens,
    }


def _metadata_event(
    *,
    route: str,
    requested_mode: str,
    actual_mode: str,
    trace_id: str,
    source_scope: str = "",
    source_usage: dict[str, bool] | None = None,
    query_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "metadata",
        "query_mode": str(query_mode or route),
        "route": route,
        "source_scope": source_scope,
        "source_usage": dict(source_usage or _source_usage_from_scope(source_scope)),
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
    source_scope: str = "",
    source_usage: dict[str, bool] | None = None,
    query_mode: str | None = None,
) -> dict[str, Any]:
    normalized_reference_objects = normalize_reference_objects(list(references or []))
    normalized_references = normalize_references(normalized_reference_objects)
    links = storage_service.build_pdf_links(normalized_references)
    resolved_query_mode = str(query_mode or route)
    resolved_source_scope = str(source_scope or (file_selection or {}).get("source_scope") or "")
    resolved_source_usage = dict(source_usage or _source_usage_from_scope(resolved_source_scope))
    return {
        "type": "done",
        "references": normalized_references,
        "reference_objects": normalized_reference_objects,
        "reference_links": links,
        "pdf_links": links,
        "doi_locations": build_doi_locations(normalized_reference_objects),
        "route": route,
        "source_scope": resolved_source_scope,
        "source_usage": resolved_source_usage,
        "used_files": list(used_files or []),
        "timings": dict(timings or {}),
        "metadata": {
            "route": route,
            "query_mode": resolved_query_mode,
            "source_scope": resolved_source_scope,
            "source_usage": resolved_source_usage,
        },
        "query_mode": resolved_query_mode,
        "trace_id": trace_id,
        "file_selection": dict(file_selection or {}),
    }


def _authority_preflight_error_response(
    *,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    exc: Exception,
) -> JSONResponse:
    status_code = 500
    code = "FASTQA_AUTHORITY_PRECONDITION_FAILED"
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = 503 if exc.response.status_code >= 500 else 502
        code = "FASTQA_AUTHORITY_HTTP_ERROR"
    elif isinstance(exc, httpx.RequestError):
        status_code = 503
        code = "FASTQA_AUTHORITY_UNAVAILABLE"
    elif isinstance(exc, ValueError):
        status_code = 502
        code = "FASTQA_AUTHORITY_CONTRACT_INVALID"
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "code": code,
            "error": str(exc) or "fastQA authority preflight failed",
            "message": str(exc) or "fastQA authority preflight failed",
            "trace_id": trace_id,
            "requested_mode": requested_mode,
            "actual_mode": actual_mode,
            "route": route,
        },
    )


def _runtime_error_event(
    *,
    code: str,
    error: str,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    source_scope: str = "",
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
        "source_scope": source_scope,
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


def _upstream_file_context(adapted_request: GatewayAskRequest) -> dict[str, Any] | None:
    route = str(adapted_request.route or "").strip()
    if route not in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
        return None

    execution_files = list(adapted_request.execution_files or [])
    used_files = list(adapted_request.used_files or adapted_request.execution_files or [])
    file_selection = dict(adapted_request.file_selection or {})

    return {
        "route_hint": route,
        "strategy": str(file_selection.get("strategy") or "gateway"),
        "selection_semantic": str(file_selection.get("selection_semantic") or "upstream_selected"),
        "turn_mode": str(adapted_request.turn_mode or file_selection.get("turn_mode") or "kb_only"),
        "allow_kb_verification": bool(adapted_request.allow_kb_verification),
        "selected_file_ids": list(adapted_request.selected_file_ids or file_selection.get("selected_file_ids") or []),
        "used_files": used_files,
        "execution_files": execution_files,
        "needs_clarification": False,
    }


def _resolve_route_context(adapted_request: GatewayAskRequest, request: Request) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    route = adapted_request.route
    file_context = _upstream_file_context(adapted_request)
    used_files = list((file_context or {}).get("used_files") or adapted_request.used_files or adapted_request.execution_files or [])
    file_selection = dict(adapted_request.file_selection or {})
    if file_context:
        file_selection = {
            **file_selection,
            "strategy": str(file_context.get("strategy") or file_selection.get("strategy") or ""),
            "selection_semantic": str(file_context.get("selection_semantic") or file_selection.get("selection_semantic") or ""),
            "selected_file_ids": list(file_context.get("selected_file_ids") or file_selection.get("selected_file_ids") or []),
        }
        file_selection["turn_mode"] = str(file_context.get("turn_mode") or adapted_request.turn_mode or file_selection.get("turn_mode") or "kb_only")
        file_selection["allow_kb_verification"] = bool(file_context.get("allow_kb_verification", adapted_request.allow_kb_verification))
    if adapted_request.source_scope:
        file_selection["source_scope"] = adapted_request.source_scope
    file_selection["kb_enabled"] = bool(adapted_request.kb_enabled)
    if adapted_request.primary_file_id is not None:
        file_selection["primary_file_id"] = adapted_request.primary_file_id
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
        conversation_context = build_conversation_context(
            current_question=adapted_request.question,
            request_chat_history=adapted_request.request_chat_history,
            authority_chat_history=adapted_request.authority_chat_history,
            authority_summary=adapted_request.authority_summary,
            authority_conversation_state=adapted_request.authority_conversation_state,
            source_scope=adapted_request.source_scope,
            selected_file_ids=adapted_request.selected_file_ids,
            used_files=adapted_request.used_files,
            execution_files=adapted_request.execution_files,
            primary_file_id=adapted_request.primary_file_id,
        )
        graph_enabled = bool(getattr(getattr(request.app.state, "settings", None), "graph_kb_enabled", False))
        graph_v2_enabled = bool(getattr(getattr(request.app.state, "settings", None), "graph_kb_v2_enabled", False))
        graph_rag_injection_enabled = bool(
            getattr(getattr(request.app.state, "settings", None), "graph_kb_rag_injection_enabled", False)
        )
        graph_client = getattr(request.app.state, "neo4j_client", None)
        graph_rag_payload: GraphRagPayload | None = None
        graph_v2_metadata: dict[str, Any] | None = None
        if graph_enabled and graph_v2_enabled:
            try:
                routing_result = route_graph_kb_v2(
                    question=adapted_request.question,
                    conversation_context=conversation_context,
                    neo4j_client=graph_client,
                    max_rows=int(getattr(getattr(request.app.state, "settings", None), "graph_kb_max_rows", 20) or 20),
                    timeout_ms=int(getattr(getattr(request.app.state, "settings", None), "graph_kb_timeout_ms", 3000) or 3000),
                    generation_runtime=getattr(request.app.state, "generation_runtime", None),
                )
                graph_v2_metadata = _graph_v2_metadata(routing_result.diagnostics, tri_state_mode=routing_result.mode)
                if routing_result.mode == "direct_answer" and routing_result.direct_result is not None and routing_result.direct_result.handled:
                    yield from _iter_graph_kb_events(
                        result=routing_result.direct_result,
                        trace_id=adapted_request.trace_id,
                        route=route,
                        extra_metadata=graph_v2_metadata,
                    )
                    return
                if routing_result.mode == "graph_for_rag" and routing_result.rag_payload is not None:
                    if graph_rag_injection_enabled:
                        graph_rag_payload = routing_result.rag_payload
                        logger.info("fastqa graph kb v2 attached graph_for_rag evidence to generation request")
                    else:
                        logger.info("fastqa graph kb v2 produced graph_for_rag evidence but rag injection is disabled")
                if routing_result.mode == "skip_graph":
                    logger.info("fastqa graph kb v2 skipped graph execution and fell through to generation")
            except Exception as exc:
                logger.warning("fastqa graph kb v2 attempt failed, falling back to generation: %s", exc)
        elif graph_enabled:
            try:
                graph_result = try_graph_kb_answer(
                    question=adapted_request.question,
                    conversation_context=conversation_context,
                    neo4j_client=graph_client,
                    max_rows=int(getattr(getattr(request.app.state, "settings", None), "graph_kb_max_rows", 20) or 20),
                    timeout_ms=int(getattr(getattr(request.app.state, "settings", None), "graph_kb_timeout_ms", 3000) or 3000),
                    generation_runtime=getattr(request.app.state, "generation_runtime", None),
                )
                if graph_result.handled:
                    yield from _iter_graph_kb_events(
                        result=graph_result,
                        trace_id=adapted_request.trace_id,
                        route=route,
                    )
                    return
            except Exception as exc:
                logger.warning("fastqa graph kb attempt failed, falling back to generation: %s", exc)

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
            recent_turns_for_llm=conversation_context["recent_turns_for_llm"],
            summary_for_llm=conversation_context["summary_for_llm"],
            conversation_state=conversation_context["conversation_state"],
            source_selection=conversation_context["source_selection"],
            graph_evidence=graph_rag_payload,
        )
        for event in qa_kb_service.iter_answer_events(
            request=qa_request,
            generation_runtime=runtime,
            redis_service=redis_service,
            sse_event=lambda event: event,
            should_cancel=should_cancel,
            logger=logger,
        ):
            yield _merge_graph_v2_event(event, graph_v2_metadata)
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
    if route == "tabular_qa":
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

    if route == "hybrid_qa":
        scope_tokens = {part.strip().lower() for part in str(adapted_request.source_scope or "").split("+") if part.strip()}
        wants_pdf_only = ("pdf" in scope_tokens) and ("table" not in scope_tokens)
        if wants_pdf_only:
            logger.info("fastqa dispatching hybrid_qa(pdf-only) to pdf handler")
            resolved_ctx = dict(file_context or {})
            # Make KB verification explicit for the PDF streaming layer.
            resolved_ctx["allow_kb_verification"] = bool(adapted_request.allow_kb_verification or adapted_request.kb_enabled)
            resolved_ctx.setdefault("turn_mode", str(adapted_request.turn_mode or "mixed"))
            yield from iter_pdf_route_events(
                app_state=request.app.state,
                adapted_request=adapted_request,
                file_context=resolved_ctx,
                sse_event=lambda event: event,
                is_cancelled=should_cancel,
            )
            return

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


def _graph_v2_metadata(diagnostics: dict[str, Any] | None, *, tri_state_mode: str | None = None) -> dict[str, Any]:
    source = dict(diagnostics or {})
    return {
        "graph_pipeline_version": str(source.get("graph_pipeline_version") or "v2"),
        "legacy_route_family": str(source.get("legacy_route_family") or source.get("legacy_route") or ""),
        "tri_state_mode": str(source.get("tri_state_mode") or tri_state_mode or ""),
        "neo4j_client": str(source.get("neo4j_client") or "neo4jgraph"),
        "doi_source": str(source.get("doi_source") or "none"),
        "legacy_template_fallback_used": bool(source.get("legacy_template_fallback_used", False)),
    }


def _merge_graph_v2_event(event: dict[str, Any], graph_v2_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not graph_v2_metadata:
        return event
    payload = dict(event or {})
    event_type = str(payload.get("type") or "").strip().lower()
    if event_type == "metadata":
        return {**graph_v2_metadata, **payload}
    if event_type == "done":
        merged_metadata = {**graph_v2_metadata, **dict(payload.get("metadata") or {})}
        payload["metadata"] = merged_metadata
        payload.setdefault("doi_source", merged_metadata.get("doi_source"))
        return payload
    return payload


def _iter_graph_kb_events(
    *,
    result: GraphKbExecutionResult,
    trace_id: str,
    route: str,
    extra_metadata: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    template_label_map = {
        "lookup_by_doi": "按 DOI 查询文献",
        "expand_doi_context_by_doi": "按 DOI 展开测试/工艺",
        "list_by_material": "按主题/材料找文献",
        "list_by_raw_material": "按原料找文献",
        "count_by_filter": "统计图谱命中文献",
    }
    template_label = template_label_map.get(str(result.template_id or "").strip(), "执行图谱检索模板")
    result_count = int(result.result_count or 0)

    yield {
        "type": "metadata",
        "query_mode": str(result.query_mode or "graph_kb"),
        "route": route,
        "trace_id": trace_id,
        **dict(extra_metadata or {}),
    }
    yield {
        "type": "step",
        "step": "graph_intent",
        "title": "阶段一",
        "detail": "识别知识图谱意图",
        "message": "阶段一：识别知识图谱意图",
        "status": "success",
        "trace_id": trace_id,
    }
    yield {
        "type": "step",
        "step": "graph_query",
        "title": "阶段二",
        "detail": template_label,
        "message": "阶段二：执行图谱检索",
        "status": "success",
        "trace_id": trace_id,
    }
    yield {
        "type": "step",
        "step": "graph_answer",
        "title": "阶段三",
        "detail": f"命中 {result_count} 条图谱结果并生成回答" if result_count > 0 else "整理图谱结果并生成回答",
        "message": "阶段三：整理图谱结果",
        "status": "success",
        "data": {"count": result_count},
        "trace_id": trace_id,
    }
    yield {
        "type": "content",
        "content": str(result.answer or ""),
        "trace_id": trace_id,
    }
    yield {
        "type": "done",
        "route": route,
        "references": list(result.references or []),
        "trace_id": trace_id,
        "metadata": dict(extra_metadata or {}),
    }


def _iter_qa_frames(*, request: Request, payload: AskRequest, adapted_request: GatewayAskRequest, limiter: Any, trace_id: str, cancel_event: Event) -> Iterator[dict[str, Any]]:
    route, file_context, used_files, file_selection = _resolve_route_context(adapted_request, request)
    requested_mode = adapted_request.requested_mode
    actual_mode = adapted_request.actual_mode or "fast"
    source_scope = str(adapted_request.source_scope or file_selection.get("source_scope") or "")
    source_usage = _source_usage_from_scope(source_scope)

    def _enrich_outbound_event(payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload or {})
        enriched["trace_id"] = str(enriched.get("trace_id") or trace_id)
        enriched["route"] = route
        enriched["source_scope"] = str(enriched.get("source_scope") or source_scope)
        enriched["source_usage"] = dict(enriched.get("source_usage") or source_usage)
        return enriched
    done_emitted = False
    metadata_emitted = False
    current_query_mode = route
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
                done_event["route"] = route
                query_mode = str(
                    done_event.get("query_mode")
                    or (
                        done_event.get("metadata").get("query_mode")
                        if isinstance(done_event.get("metadata"), dict)
                        else ""
                    )
                    or current_query_mode
                    or route
                )
                if not metadata_emitted:
                    metadata_emitted = True
                    yield _metadata_event(
                        route=str(done_event.get("route") or route),
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        trace_id=trace_id,
                        source_scope=source_scope,
                        source_usage=source_usage,
                        query_mode=query_mode,
                    )
                raw_reference_objects = done_event.get("reference_objects")
                normalized_reference_objects = normalize_reference_objects(
                    raw_reference_objects if isinstance(raw_reference_objects, list) else done_event.get("references")
                )
                normalized_references = normalize_references(normalized_reference_objects)
                links = storage_service.build_pdf_links(normalized_references)
                done_event["references"] = normalized_references
                done_event["reference_objects"] = normalized_reference_objects
                done_event["reference_links"] = links
                done_event["pdf_links"] = links
                done_event["doi_locations"] = build_doi_locations(normalized_reference_objects)
                done_event.setdefault("timings", {})
                done_event.setdefault("trace_id", trace_id)
                done_event.setdefault("query_mode", query_mode)
                done_event["used_files"] = list(done_event.get("used_files") or used_files)
                done_event["file_selection"] = dict(done_event.get("file_selection") or file_selection)
                done_event.setdefault("source_scope", source_scope)
                done_event.setdefault("source_usage", dict(source_usage))
                done_event["metadata"] = {
                    **dict(done_event.get("metadata") or {}),
                    "requested_mode": requested_mode,
                    "actual_mode": actual_mode,
                    "route": route,
                    "query_mode": query_mode,
                    "source_scope": str(done_event.get("source_scope") or source_scope),
                    "source_usage": dict(done_event.get("source_usage") or source_usage),
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
                current_query_mode = query_mode
                yield _enrich_outbound_event(done_event)
                continue
            if event_type == "metadata":
                metadata_event = dict(event)
                metadata_event.setdefault("requested_mode", requested_mode)
                metadata_event.setdefault("actual_mode", actual_mode)
                metadata_event.setdefault("route", route)
                metadata_event.setdefault("trace_id", trace_id)
                metadata_event.setdefault("source_scope", source_scope)
                metadata_event.setdefault("source_usage", dict(source_usage))
                current_query_mode = str(metadata_event.get("query_mode") or current_query_mode or route)
                metadata_emitted = True
                yield _enrich_outbound_event(metadata_event)
                continue
            if event_type == "error":
                error_event = dict(event)
                error_event.setdefault("trace_id", trace_id)
                error_event.setdefault("requested_mode", requested_mode)
                error_event.setdefault("actual_mode", actual_mode)
                error_event.setdefault("route", route)
                error_event.setdefault("source_scope", source_scope)
                if not metadata_emitted:
                    metadata_emitted = True
                    yield _metadata_event(
                        route=route,
                        requested_mode=requested_mode,
                        actual_mode=actual_mode,
                        trace_id=trace_id,
                        source_scope=source_scope,
                        source_usage=source_usage,
                        query_mode=_event_query_mode(error_event, current_query_mode or route),
                    )
                logger.warning(
                    "fastqa error event route=%s code=%s error=%s",
                    error_event.get("route") or route,
                    error_event.get("code") or "",
                    error_event.get("error") or error_event.get("message") or "",
                )
                yield _enrich_outbound_event(error_event)
                return
            yield _enrich_outbound_event(dict(event))
        if not cancel_event.is_set() and not done_emitted:
            if not metadata_emitted:
                yield _metadata_event(
                    route=route,
                    requested_mode=requested_mode,
                    actual_mode=actual_mode,
                    trace_id=trace_id,
                    source_scope=source_scope,
                    source_usage=source_usage,
                    query_mode=current_query_mode or route,
                )
            logger.info("fastqa stream finished without explicit done event; emitting synthetic done route=%s", route)
            yield _done_event(
                route=route,
                used_files=used_files,
                trace_id=trace_id,
                file_selection=file_selection,
                source_scope=source_scope,
                source_usage=source_usage,
                query_mode=current_query_mode or route,
            )
    except Exception as exc:
        logger.error("fastqa stream execution failed route=%s error=%s", route, exc, exc_info=True)
        if not metadata_emitted:
            yield _metadata_event(
                route=route,
                requested_mode=requested_mode,
                actual_mode=actual_mode,
                trace_id=trace_id,
                source_scope=source_scope,
                source_usage=source_usage,
                query_mode=current_query_mode or route,
            )
        yield _runtime_error_event(
            code="FASTQA_RUNTIME_ERROR",
            error=f"fastQA 执行异常: {exc}",
            trace_id=trace_id,
            route=route,
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            source_scope=source_scope,
            detail={"exception_type": exc.__class__.__name__, "failure_stage": "runtime_prepare", "retriable": True},
        )
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
        terminal_persisted = False
        try:
            for payload in tap.wrap(source):
                event_type = str(payload.get("type") or "").strip().lower()
                if event_type == "done" and not terminal_persisted:
                    terminal_persisted = True
                    _persist_assistant_terminal_if_needed(
                        request=request,
                        adapted_request=adapted_request,
                        tap=tap,
                        route=route,
                        trace_id=trace_id,
                        terminal_status="done",
                    )
                elif event_type == "error" and not terminal_persisted:
                    terminal_persisted = True
                    terminal_status = "canceled" if _is_cancel_error(payload) else "failed"
                    _persist_assistant_terminal_if_needed(
                        request=request,
                        adapted_request=adapted_request,
                        tap=tap,
                        route=route,
                        trace_id=trace_id,
                        terminal_status=terminal_status,
                        error_payload=payload,
                    )
                yield payload
        finally:
            _log_stream_summary(request=request, tap=tap, trace_id=trace_id, route=route)

    return _iter()


def _collect_sync_result(events: list[dict[str, Any]], *, trace_id: str, requested_mode: str, actual_mode: str, route: str, used_files: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    contents: list[str] = []
    references: list[str] = []
    reference_objects: list[dict[str, Any]] = []
    timings: dict[str, Any] = {}
    metadata: dict[str, Any] = {"requested_mode": requested_mode, "actual_mode": actual_mode, "route": route, "query_mode": route, "source_scope": "", "source_usage": _source_usage_from_scope("")}
    error_payload: dict[str, Any] | None = None
    file_selection: dict[str, Any] = {}
    source_scope = ""
    source_usage = _source_usage_from_scope(source_scope)
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
            source_scope = str(event.get("source_scope") or metadata.get("source_scope") or file_selection.get("source_scope") or source_scope)
            source_usage = dict(event.get("source_usage") or metadata.get("source_usage") or _source_usage_from_scope(source_scope))
        elif event_type == "metadata":
            metadata = {**metadata, **dict(event)}
            source_scope = str(event.get("source_scope") or metadata.get("source_scope") or source_scope)
            source_usage = dict(event.get("source_usage") or metadata.get("source_usage") or _source_usage_from_scope(source_scope))
        elif event_type == "error" and error_payload is None:
            error_payload = dict(event)
    links = storage_service.build_pdf_links(references)
    payload = {
        "success": error_payload is None,
        "final_answer": "".join(contents),
        "query_mode": metadata.get("query_mode") or route,
        "route": metadata.get("route") or route,
        "source_scope": source_scope,
        "source_usage": source_usage,
        "timings": timings,
        "references": references,
        "reference_objects": reference_objects,
        "reference_links": links,
        "pdf_links": links,
        "doi_locations": build_doi_locations(reference_objects),
        "metadata": {**metadata, "source_scope": source_scope, "source_usage": source_usage},
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
    try:
        _persist_user_message_if_needed(request=request, adapted_request=adapted_request, route=route, trace_id=trace_id)
        adapted_request = _load_conversation_context_if_needed(
            request=request,
            adapted_request=adapted_request,
            route=route,
            trace_id=trace_id,
        )
    except Exception as exc:
        limiter.release()
        return _authority_preflight_error_response(
            trace_id=trace_id,
            route=route,
            requested_mode=adapted_request.requested_mode,
            actual_mode=adapted_request.actual_mode,
            exc=exc,
        )
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
    try:
        _persist_user_message_if_needed(request=request, adapted_request=adapted_request, route=route, trace_id=trace_id)
        adapted_request = _load_conversation_context_if_needed(
            request=request,
            adapted_request=adapted_request,
            route=route,
            trace_id=trace_id,
        )
    except Exception as exc:
        limiter.release()
        return _authority_preflight_error_response(
            trace_id=trace_id,
            route=route,
            requested_mode=adapted_request.requested_mode,
            actual_mode=adapted_request.actual_mode,
            exc=exc,
        )
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
