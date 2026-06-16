"""Shared file-route gate payloads for ask and task entrypoints."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def file_status_http_code(status_code: str) -> int:
    if status_code == "FILE_NOT_FOUND":
        return 404
    return 409


def route_context_payload(route_decision) -> dict[str, Any]:
    return {
        "source_scope": route_decision.source_scope,
        "selected_file_ids": list(route_decision.selected_file_ids or []),
        "strategy": route_decision.strategy,
        "file_selection": dict(route_decision.file_selection or {}),
        "route_reasons": list(route_decision.route_reasons or []),
        "route_confidence": route_decision.route_confidence,
        "classifier_used": route_decision.classifier_used,
    }


def build_file_status_payload(*, trace_id: str, route_decision) -> dict[str, Any]:
    return {
        "success": False,
        "code": route_decision.status_code,
        "error": route_decision.status_error or "file_state_blocked",
        "message": route_decision.status_message or route_decision.status_code,
        "trace_id": trace_id,
        "requested_mode": route_decision.requested_mode,
        "actual_mode": route_decision.actual_mode,
        "route": route_decision.route,
        "retriable": route_decision.status_retriable,
        "detail": {
            **dict(route_decision.status_detail or {}),
            **route_context_payload(route_decision),
        },
    }


def file_status_json_response(*, trace_id: str, route_decision) -> JSONResponse:
    return JSONResponse(
        status_code=file_status_http_code(str(route_decision.status_code or "")),
        content=build_file_status_payload(trace_id=trace_id, route_decision=route_decision),
    )
