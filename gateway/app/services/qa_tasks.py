"""User-facing QA task contracts backed by gateway admission state."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
import time
from typing import Any
from uuid import uuid4

import anyio
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.auth import AuthContext
from app.models.ask import AskRequest
from app.services.execution_admission import (
    AdmissionExecutionOutcome,
    ExecutionAdmissionDispatcher,
    ExecutionAdmissionWorker,
    evaluate_task_create_admission,
    normalize_public_task_status,
)
from app.services.sse_frames import SSEFrameBuffer, parse_sse_json_frame


logger = logging.getLogger(__name__)


_FILE_ROUTES = {"pdf_qa", "tabular_qa", "hybrid_qa"}
_TERMINAL_TASK_STATUSES = {"completed", "failed", "canceled", "expired"}
_RELAY_RETENTION_FLOOR_SECONDS = 60
_TERMINAL_SYNCABLE_STATUSES = {"completed", "failed", "canceled", "expired"}
_TASK_EVENT_STREAM_POLL_SECONDS = 0.1
_PROGRESS_FLUSH_MAX_PENDING_EVENTS = 5
_PROGRESS_FLUSH_MAX_IDLE_SECONDS = 0.25
_CANCEL_CLEANUP_TIMEOUT_SECONDS = 0.5
_PATENT_STREAM_CAPABILITY_HEADER = b"x-patent-stream-capability"
_PATENT_STREAM_CAPABILITY_OPTION = "patent_stream_capability"
_PATENT_STREAM_CAPABILITY_PREVIEW_V1 = "preview_v1"


def _format_log_fields(**fields: Any) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _log_task_event(event: str, **fields: Any) -> None:
    suffix = _format_log_fields(**fields)
    logger.info("gateway %s%s", event, f" {suffix}" if suffix else "")


def _is_truthy_env_flag(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "debug"}


def _task_events_debug_enabled() -> bool:
    return _is_truthy_env_flag(os.getenv("GATEWAY_TASK_EVENTS_DEBUG"))


def _summarize_public_event_batch(events: list[dict[str, Any]]) -> dict[str, Any]:
    first = events[0] if events else None
    last = events[-1] if events else None
    type_counts: dict[str, int] = {}
    content_chars = 0
    for event in events:
        event_type = str(event.get("type") or "").strip().lower() or "unknown"
        type_counts[event_type] = int(type_counts.get(event_type) or 0) + 1
        if event_type == "content":
            content_chars += len(str(event.get("content") or event.get("delta") or ""))
    return {
        "count": len(events),
        "first_seq": int(first.get("seq") or 0) if isinstance(first, dict) else 0,
        "last_seq": int(last.get("seq") or 0) if isinstance(last, dict) else 0,
        "first_type": str(first.get("type") or "").strip().lower() if isinstance(first, dict) else "",
        "last_type": str(last.get("type") or "").strip().lower() if isinstance(last, dict) else "",
        "content_chars": content_chars,
        "type_counts": type_counts,
    }


def _relay_payload_is_terminal(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    event_type = str(payload.get("type") or "").strip().lower()
    if event_type in {"done", "error"}:
        return True
    return event_type == "state" and normalize_public_task_status(payload.get("status")) in _TERMINAL_TASK_STATUSES


def _relay_frames_have_terminal(frames: list[dict[str, Any]]) -> bool:
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        payload = frame.get("payload")
        if _relay_payload_is_terminal(payload if isinstance(payload, dict) else None):
            return True
    return False


def _normalized_positive_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except Exception:
        return None
    return normalized if normalized > 0 else None


def _epoch_ms() -> int:
    return max(0, int(time.time() * 1000))


def _normalized_epoch_ms(value: Any) -> int | None:
    return _normalized_positive_int(value)


def _merge_task_telemetry(*sources: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            normalized = _normalized_epoch_ms(value)
            if normalized is None:
                continue
            merged[str(key)] = normalized
    return merged


def _public_task_telemetry(record: dict[str, Any]) -> dict[str, int]:
    telemetry = _merge_task_telemetry(record.get("telemetry"))
    public: dict[str, int] = {}
    for field_name in (
        "accepted_at_ms",
        "dispatch_started_at_ms",
        "backend_stream_opened_at_ms",
        "first_step_at_ms",
        "first_content_at_ms",
    ):
        normalized = _normalized_epoch_ms(telemetry.get(field_name))
        if normalized is not None:
            public[field_name] = normalized

    def _append_latency(metric_name: str, *, start_field: str, end_field: str) -> None:
        start = _normalized_epoch_ms(public.get(start_field))
        end = _normalized_epoch_ms(public.get(end_field))
        if start is None or end is None or end < start:
            return
        public[metric_name] = end - start

    _append_latency(
        "accepted_to_first_step_ms",
        start_field="accepted_at_ms",
        end_field="first_step_at_ms",
    )
    _append_latency(
        "dispatch_to_first_step_ms",
        start_field="dispatch_started_at_ms",
        end_field="first_step_at_ms",
    )
    _append_latency(
        "accepted_to_first_content_ms",
        start_field="accepted_at_ms",
        end_field="first_content_at_ms",
    )
    return public


def _normalized_patent_stream_capability(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == _PATENT_STREAM_CAPABILITY_PREVIEW_V1:
        return normalized
    return ""


def _resolve_task_patent_stream_capability(request: dict[str, Any]) -> str:
    actual_mode = str(request.get("actual_mode") or "").strip().lower()
    route = str(request.get("route") or (request.get("execution_snapshot") or {}).get("route") or "").strip().lower()
    if actual_mode != "patent" or route not in _FILE_ROUTES:
        return ""
    execution_snapshot = dict(request.get("execution_snapshot") or {})
    options = execution_snapshot.get("options") if isinstance(execution_snapshot.get("options"), dict) else {}
    if not options and isinstance(request.get("options"), dict):
        options = dict(request.get("options") or {})
    return _normalized_patent_stream_capability(options.get(_PATENT_STREAM_CAPABILITY_OPTION))


def _task_content_persists_in_main_body(payload: dict[str, Any]) -> bool:
    return str(payload.get("content_role") or "").strip().lower() != "preview"


def _live_runtime_handle(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("handle")
    return entry


def _live_runtime_cancel_event(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("cancel_event")
    return None


def _live_runtime_owner_loop(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("owner_loop")
    return None


def _set_live_runtime_cancelled(entry: Any) -> None:
    cancel_event = _live_runtime_cancel_event(entry)
    if cancel_event is not None and hasattr(cancel_event, "set"):
        cancel_event.set()


def _live_runtime_flush_hook(entry: Any):
    if isinstance(entry, dict):
        return entry.get("flush_progress")
    return getattr(entry, "flush_progress", None)


def _live_runtime_terminal_snapshot(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"answer_text": "", "steps": []}
    runtime_lock = entry.get("lock")
    if runtime_lock is None:
        return {
            "answer_text": str(entry.get("answer_text") or ""),
            "steps": list(entry.get("latest_steps") or []),
        }
    with runtime_lock:
        return {
            "answer_text": str(entry.get("answer_text") or ""),
            "steps": list(entry.get("latest_steps") or []),
        }


def _quota_type_for_route_name(route_name: Any) -> str:
    return "file_qa" if str(route_name or "").strip().lower() in _FILE_ROUTES else "ask_query"


class QATaskService:
    def __init__(self, request: Request) -> None:
        self.request = request
        self.app = request.app
        self.settings = request.app.state.settings
        self.queue_store = request.app.state.execution_queue_status_store
        self.relay_store = request.app.state.execution_event_relay_store
        self.slot_lease_store = request.app.state.execution_slot_lease_store

    async def create_task(self, payload: AskRequest, *, auth_context: AuthContext) -> dict[str, Any]:
        started = time.perf_counter()
        bound_payload = self._bind_payload_to_authenticated_user(payload, auth_context=auth_context)
        self._assert_requested_mode_enabled(bound_payload)
        conversation_id = self._require_positive_int(bound_payload.conversation_id, detail="task_conversation_id_required")
        user_id = self._require_positive_int(bound_payload.user_id, detail="task_user_id_required")
        route_decision, file_context = await self._resolve_route(bound_payload)
        _log_task_event(
            "task create accepted",
            conversation_id=conversation_id,
            user_id=user_id,
            requested_mode=bound_payload.requested_mode,
            actual_mode=route_decision.actual_mode,
            route=route_decision.route,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        self._assert_route_enabled(route_decision)
        lock_manager = getattr(self.app.state, "distributed_lock_manager", None)
        lock_handle = None
        if lock_manager is not None:
            lock_handle = lock_manager.acquire(
                "tasks",
                "create",
                owner=f"user:{user_id}:conversation:{conversation_id}",
                ttl_seconds=30,
                wait_timeout_seconds=5.0,
            )
            if lock_handle is None:
                raise HTTPException(status_code=503, detail="task_create_busy")
        try:
            self._assert_task_create_admission(bound_payload)
            await self._assert_backend_ready(route_decision.actual_mode)
            task_id = f"task_{uuid4().hex}"
            created_at = datetime.now(timezone.utc)
            accepted_at_ms = max(0, int(created_at.timestamp() * 1000))
            expires_at = created_at + timedelta(seconds=int(self.settings.admission.queued_ttl_seconds))
            persistence_service = self.app.state.conversation_persistence_service
            quota_proxy = self.app.state.quota_proxy_service
            user_message_id = ""
            assistant_message_id = ""
            side_effects_started = False
            request_record_started = False
            quota_type = _quota_type_for_route_name(route_decision.route)
            quota_grant_id = ""
            downstream_authorization = self._downstream_authorization_header()
            try:
                precheck = await quota_proxy.precheck(
                    request=self.request,
                    user_id=user_id,
                    quota_type=quota_type,
                    strict_config=False,
                )
                if not precheck.success:
                    raise HTTPException(
                        status_code=int(precheck.status_code or 503),
                        detail=str(precheck.payload.get("error") or "quota_precheck_failed"),
                    )
                grant_data = precheck.payload.get("data") if isinstance(precheck.payload.get("data"), dict) else {}
                quota_grant_id = str(grant_data.get("grant_id") or "").strip()
                _log_task_event(
                    "task create quota precheck completed",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    requested_mode=bound_payload.requested_mode,
                    actual_mode=route_decision.actual_mode,
                    route=route_decision.route,
                    quota_type=quota_type,
                    grant_id=quota_grant_id or "-",
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                record = {
                    "request_id": task_id,
                    "client_request_id": str(bound_payload.client_request_id or "").strip() or None,
                    "status": "provisioning",
                    "persisted_last_seq": 0,
                    "conversation_id": bound_payload.conversation_id,
                    "assistant_message_id": None,
                    "requested_mode": bound_payload.requested_mode,
                    "actual_mode": route_decision.actual_mode,
                    "target_backend": route_decision.actual_mode,
                    "route": route_decision.route,
                    "turn_mode": route_decision.turn_mode,
                    "source_scope": route_decision.source_scope,
                    "kb_enabled": route_decision.kb_enabled,
                    "allow_kb_verification": route_decision.allow_kb_verification,
                    "selected_file_ids": list(route_decision.selected_file_ids or []),
                    "execution_files": list(route_decision.execution_files or []),
                    "queue_tier": self._queue_tier(route_decision.actual_mode),
                    "created_at": created_at.isoformat(),
                    "updated_at": created_at.isoformat(),
                    "enqueued_at": created_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "telemetry": {"accepted_at_ms": accepted_at_ms},
                    "cancel_allowed": False,
                    "transport_kind": "sse",
                    "user_id": bound_payload.user_id,
                    "quota_type": quota_type,
                    "quota_grant_id": quota_grant_id or None,
                    "downstream_authorization": downstream_authorization or None,
                    "execution_snapshot": {
                        "question": bound_payload.question,
                        "conversation_id": bound_payload.conversation_id,
                        "user_id": bound_payload.user_id,
                        "chat_history": [item.model_dump() for item in bound_payload.chat_history],
                        "requested_mode": bound_payload.requested_mode,
                        "actual_mode": route_decision.actual_mode,
                        "route": route_decision.route,
                        "source_scope": route_decision.source_scope,
                        "turn_mode": route_decision.turn_mode,
                        "kb_enabled": route_decision.kb_enabled,
                        "allow_kb_verification": route_decision.allow_kb_verification,
                        "needs_clarification": route_decision.needs_clarification,
                        "used_files": list(file_context.used_files or []),
                        "execution_files": list(route_decision.execution_files or []),
                        "selected_file_ids": list(route_decision.selected_file_ids or []),
                        "strategy": route_decision.strategy,
                        "primary_file_id": route_decision.primary_file_id,
                        "file_selection": dict(route_decision.file_selection or {}),
                        "route_reasons": list(route_decision.route_reasons or []),
                        "route_confidence": route_decision.route_confidence,
                        "classifier_used": route_decision.classifier_used,
                        "task_id": task_id,
                        "client_request_id": str(bound_payload.client_request_id or "").strip() or None,
                        "user_message_id": "",
                        "assistant_message_id": "",
                        "pdf_context": dict(bound_payload.pdf_context or {}),
                        "quota_type": quota_type,
                        "quota_grant_id": quota_grant_id or None,
                        "downstream_authorization": downstream_authorization or None,
                        "options": dict(bound_payload.options or {}),
                    },
                }
                stored = self.queue_store.put_request(record, ttl_seconds=int(self.settings.admission.queued_ttl_seconds))
                if not stored:
                    raise HTTPException(status_code=500, detail="task_create_failed")
                request_record_started = True
                created_turn = await persistence_service.create_task_turn(
                    request=self.request,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                    content=bound_payload.question,
                    route=route_decision.route,
                    requested_mode=bound_payload.requested_mode,
                    actual_mode=route_decision.actual_mode,
                    selected_file_ids=self._selected_file_ids(bound_payload.pdf_context),
                    status="queued",
                    last_seq=0,
                )
                side_effects_started = True
                user_message_id = str(created_turn.get("user_message_id") or "")
                if not user_message_id:
                    raise HTTPException(status_code=500, detail="task_create_failed")
                assistant_message_id = str(created_turn.get("assistant_message_id") or "")
                if not assistant_message_id:
                    raise HTTPException(status_code=500, detail="task_create_failed")
                _log_task_event(
                    "task create authority turn persisted",
                    task_id=task_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    route=route_decision.route,
                    requested_mode=bound_payload.requested_mode,
                    actual_mode=route_decision.actual_mode,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                updated_record = dict(record)
                updated_record["status"] = "queued"
                updated_record["cancel_allowed"] = True
                updated_record["assistant_message_id"] = assistant_message_id or None
                execution_snapshot = dict(updated_record.get("execution_snapshot") or {})
                execution_snapshot["user_message_id"] = user_message_id
                execution_snapshot["assistant_message_id"] = assistant_message_id
                updated_record["execution_snapshot"] = execution_snapshot
                stored = self.queue_store.put_request(updated_record, ttl_seconds=int(self.settings.admission.queued_ttl_seconds))
                if not stored:
                    raise HTTPException(status_code=500, detail="task_create_failed")
                self._append_state_frame(task_id, status="queued")
                _log_task_event(
                    "task queued",
                    task_id=task_id,
                    client_request_id=str(bound_payload.client_request_id or "").strip() or "-",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    requested_mode=bound_payload.requested_mode,
                    actual_mode=route_decision.actual_mode,
                    route=route_decision.route,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            except httpx.HTTPStatusError as exc:
                cleanup_error = await self._cleanup_failed_task_create(
                    payload=bound_payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    side_effects_started=side_effects_started,
                    request_record_started=request_record_started,
                    quota_grant_id=quota_grant_id,
                )
                if cleanup_error is not None:
                    raise cleanup_error
                raise HTTPException(
                    status_code=int(exc.response.status_code or 503),
                    detail="task_create_failed",
                ) from exc
            except ValueError as exc:
                cleanup_error = await self._cleanup_failed_task_create(
                    payload=bound_payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    side_effects_started=side_effects_started,
                    request_record_started=request_record_started,
                    quota_grant_id=quota_grant_id,
                )
                if cleanup_error is not None:
                    raise cleanup_error
                raise HTTPException(status_code=400, detail=str(exc) or "task_create_invalid") from exc
            except HTTPException:
                cleanup_error = await self._cleanup_failed_task_create(
                    payload=bound_payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    side_effects_started=side_effects_started,
                    request_record_started=request_record_started,
                    quota_grant_id=quota_grant_id,
                )
                if cleanup_error is not None:
                    raise cleanup_error
                raise
            except httpx.HTTPError as exc:
                cleanup_error = await self._cleanup_failed_task_create(
                    payload=bound_payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    side_effects_started=side_effects_started,
                    request_record_started=request_record_started,
                    quota_grant_id=quota_grant_id,
                )
                if cleanup_error is not None:
                    raise cleanup_error
                raise HTTPException(status_code=503, detail="task_create_failed") from exc
            except Exception as exc:
                cleanup_error = await self._cleanup_failed_task_create(
                    payload=bound_payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    side_effects_started=side_effects_started,
                    request_record_started=request_record_started,
                    quota_grant_id=quota_grant_id,
                )
                if cleanup_error is not None:
                    raise cleanup_error
                raise HTTPException(status_code=500, detail="task_create_failed") from exc
        finally:
            if lock_manager is not None:
                lock_manager.release(lock_handle)
        return self.build_task_summary(task_id, auth_context=auth_context)

    async def get_task(self, task_id: str, *, auth_context: AuthContext) -> dict[str, Any]:
        await self._ensure_task_record_ready(task_id, auth_context=auth_context)
        return self.build_task_summary(task_id, auth_context=auth_context)

    async def get_task_events(self, task_id: str, *, after_seq: int, auth_context: AuthContext) -> dict[str, Any]:
        await self._ensure_task_record_ready(task_id, auth_context=auth_context)
        summary = self.build_task_summary(task_id, auth_context=auth_context)
        frames = self.relay_store.get_frames(task_id, after_sequence=after_seq)
        events = [self._frame_to_public_event(summary=summary, frame=frame) for frame in frames]
        return {
            "success": True,
            "task_id": task_id,
            "after_seq": int(after_seq),
            "events": events,
        }

    async def stream_task_events(self, task_id: str, *, after_seq: int, auth_context: AuthContext) -> StreamingResponse:
        await self._ensure_task_record_ready(task_id, auth_context=auth_context)
        self._try_fast_dispatch_streaming_task(task_id, auth_context=auth_context)
        self.build_task_summary(task_id, auth_context=auth_context)
        debug_enabled = _task_events_debug_enabled()

        async def event_stream():
            next_after = int(after_seq)
            if debug_enabled:
                await self._ensure_task_record_ready(task_id, auth_context=auth_context)
                initial_summary = self.build_task_summary(task_id, auth_context=auth_context)
                initial_relay_state = self.relay_store.describe_request(task_id)
                logger.info(
                    "gateway task events stream start task_id=%s after_seq=%s status=%s terminal=%s latest_seq=%s replay_available=%s",
                    task_id,
                    next_after,
                    str(initial_summary.get("status") or ""),
                    bool(initial_summary.get("terminal")),
                    int(initial_relay_state.get("latest_sequence") or 0),
                    bool(initial_summary.get("replay_available")),
                )
            while True:
                await self._ensure_task_record_ready(task_id, auth_context=auth_context)
                summary = self.build_task_summary(task_id, auth_context=auth_context)
                frames = self.relay_store.get_frames(task_id, after_sequence=next_after)
                replay_has_terminal = _relay_frames_have_terminal(frames)
                events = [self._frame_to_public_event(summary=summary, frame=frame) for frame in frames]
                if debug_enabled and events:
                    logger.info(
                        "gateway task events stream batch task_id=%s after_seq=%s status=%s batch=%s",
                        task_id,
                        next_after,
                        str(summary.get("status") or ""),
                        _summarize_public_event_batch(events),
                    )
                for event in events:
                    next_after = max(next_after, int(event.get("seq") or 0))
                    yield self._encode_sse_event(event)
                relay_state = self.relay_store.describe_request(task_id)
                latest_seq = int(relay_state.get("latest_sequence") or 0)
                relay_has_terminal = replay_has_terminal
                if bool(summary.get("terminal")) and latest_seq <= next_after:
                    if not relay_has_terminal:
                        relay_has_terminal = _relay_frames_have_terminal(
                            self.relay_store.get_frames(task_id, after_sequence=0)
                        )
                    if not relay_has_terminal:
                        terminal_status = normalize_public_task_status(summary.get("status"))
                        appended = self._append_state_frame(task_id, status=terminal_status)
                        if not bool(appended.get("ignored")):
                            continue
                        relay_has_terminal = _relay_frames_have_terminal(
                            self.relay_store.get_frames(task_id, after_sequence=0)
                        )
                    if not relay_has_terminal:
                        continue
                    if debug_enabled:
                        logger.info(
                            "gateway task events stream stop task_id=%s reason=terminal-drained status=%s latest_seq=%s next_after=%s",
                            task_id,
                            str(summary.get("status") or ""),
                            latest_seq,
                            next_after,
                        )
                    break
                if await self.request.is_disconnected():
                    if debug_enabled:
                        logger.info(
                            "gateway task events stream stop task_id=%s reason=client-disconnected status=%s latest_seq=%s next_after=%s",
                            task_id,
                            str(summary.get("status") or ""),
                            latest_seq,
                            next_after,
                        )
                    break
                await anyio.sleep(_TASK_EVENT_STREAM_POLL_SECONDS)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    async def cancel_task(self, task_id: str, *, auth_context: AuthContext) -> dict[str, Any]:
        await self._ensure_task_record_ready(task_id, auth_context=auth_context)
        record = self._get_owned_task_record(task_id, auth_context=auth_context)
        public_status = normalize_public_task_status(record.get("status"))
        if public_status in _TERMINAL_TASK_STATUSES:
            self._assert_no_live_lease(task_id, record=record)
            return self.build_task_summary(task_id, auth_context=auth_context)
        cancelled_at = datetime.now(timezone.utc).isoformat()
        cancelled = self._cancel_with_conflict_recovery(task_id, cancelled_at=cancelled_at)
        if cancelled is None:
            raise HTTPException(status_code=409, detail="task_not_cancellable")
        cancelled_status = normalize_public_task_status(cancelled.get("status"))
        if cancelled_status in _TERMINAL_TASK_STATUSES and cancelled_status != "canceled":
            self._assert_no_live_lease(task_id, record=cancelled)
            return self.build_task_summary(task_id, auth_context=auth_context)
        live_runtime = self._get_live_runtime(task_id)
        _set_live_runtime_cancelled(live_runtime)
        await self._flush_live_progress(task_id, entry=live_runtime)
        terminal_snapshot = _live_runtime_terminal_snapshot(live_runtime)
        terminal_answer_text = str(terminal_snapshot.get("answer_text") or "")
        terminal_steps = list(terminal_snapshot.get("steps") or [])
        terminal_seq = self._append_state_frame(task_id, status="canceled")["sequence"]
        await self._abort_live_stream(task_id)
        terminal_side_effect_succeeded = False
        if _normalized_positive_int(cancelled.get("conversation_id")) and _normalized_positive_int(cancelled.get("user_id")):
            try:
                await self.app.state.conversation_persistence_service.terminal_task_assistant(
                    request=self.request,
                    conversation_id=cancelled.get("conversation_id"),
                    user_id=cancelled.get("user_id"),
                    task_id=task_id,
                    terminal_status="canceled",
                    last_seq=int(terminal_seq),
                    answer_text=terminal_answer_text,
                    steps=terminal_steps,
                    failure={},
                )
                terminal_side_effect_succeeded = True
            except Exception:
                logger.warning("gateway task cancel terminal sync failed task_id=%s", task_id, exc_info=True)
        quota_side_effect_succeeded = False
        try:
            quota_result = await self._finalize_quota_grant(grant_id=str(cancelled.get("quota_grant_id") or ""), success=False)
            quota_side_effect_succeeded = quota_result is None or bool(quota_result.success)
        except Exception:
            logger.warning("gateway task cancel quota finalize failed task_id=%s", task_id, exc_info=True)
        if not terminal_side_effect_succeeded or not quota_side_effect_succeeded:
            self._mark_terminal_sync_pending(
                record=cancelled,
                terminal_status="canceled",
                last_seq=int(terminal_seq),
                answer_text=terminal_answer_text,
                steps=terminal_steps,
                failure={},
                quota_success=False,
            )
        self._assert_no_live_lease(task_id, record=cancelled)
        return self.build_task_summary(task_id, auth_context=auth_context)

    def build_task_summary(self, task_id: str, *, auth_context: AuthContext | None = None) -> dict[str, Any]:
        if auth_context is None:
            record = self.queue_store.get_request(task_id)
            if record is None:
                raise HTTPException(status_code=404, detail="task_not_found")
        else:
            record = self._get_owned_task_record(task_id, auth_context=auth_context)
        return self._build_public_summary(record)

    async def _ensure_task_record_ready(self, task_id: str, *, auth_context: AuthContext | None = None) -> dict[str, Any]:
        if auth_context is None:
            record = self.queue_store.get_request(task_id)
            if record is None:
                raise HTTPException(status_code=404, detail="task_not_found")
        else:
            record = self._get_owned_task_record(task_id, auth_context=auth_context)
        public_status = normalize_public_task_status(record.get("status"))
        if public_status != "provisioning":
            return record
        recovered = await self._recover_provisioning_task_record(record)
        return recovered if isinstance(recovered, dict) else record

    async def _recover_provisioning_task_record(self, record: dict[str, Any]) -> dict[str, Any]:
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            return record
        lock_manager = getattr(self.app.state, "distributed_lock_manager", None)
        lock_handle = None
        if lock_manager is not None:
            lock_handle = lock_manager.acquire(
                "tasks",
                "provisioning-recover",
                request_id,
                owner=f"task:{request_id}:recover",
                ttl_seconds=10,
                wait_timeout_seconds=1.0,
            )
            if lock_handle is None:
                current = self.queue_store.get_request(request_id)
                return current if isinstance(current, dict) else record
        snapshot = dict(record.get("execution_snapshot") or {})
        try:
            current = self.queue_store.get_request(request_id)
            if not isinstance(current, dict):
                return record
            if normalize_public_task_status(current.get("status")) != "provisioning":
                return current
            snapshot = dict(current.get("execution_snapshot") or snapshot)
            conversation_id = _normalized_positive_int(current.get("conversation_id") or snapshot.get("conversation_id"))
            user_id = _normalized_positive_int(current.get("user_id") or snapshot.get("user_id"))
            question = str(snapshot.get("question") or "").strip()
            route = str(current.get("route") or snapshot.get("route") or "").strip() or "kb_qa"
            requested_mode = str(current.get("requested_mode") or snapshot.get("requested_mode") or "").strip()
            actual_mode = str(current.get("actual_mode") or snapshot.get("actual_mode") or "").strip()
            selected_file_ids = list(snapshot.get("selected_file_ids") or current.get("selected_file_ids") or [])
            if conversation_id is None or user_id is None or not question or not requested_mode or not actual_mode:
                return current
            try:
                created_turn = await self.app.state.conversation_persistence_service.create_task_turn(
                    request=self.request,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=request_id,
                    content=question,
                    route=route,
                    requested_mode=requested_mode,
                    actual_mode=actual_mode,
                    selected_file_ids=selected_file_ids,
                    status="queued",
                    last_seq=0,
                )
            except Exception:
                logger.warning("gateway task provisioning recover failed task_id=%s", request_id, exc_info=True)
                return current
            user_message_id = str(created_turn.get("user_message_id") or "").strip()
            assistant_message_id = str(created_turn.get("assistant_message_id") or "").strip()
            if not user_message_id or not assistant_message_id:
                return current
            ttl_seconds = self.queue_store.request_ttl_seconds(request_id) or int(self.settings.admission.queued_ttl_seconds)
            updated = dict(current)
            updated["status"] = "queued"
            updated["cancel_allowed"] = True
            updated["assistant_message_id"] = assistant_message_id
            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            execution_snapshot = dict(updated.get("execution_snapshot") or {})
            execution_snapshot["user_message_id"] = user_message_id
            execution_snapshot["assistant_message_id"] = assistant_message_id
            updated["execution_snapshot"] = execution_snapshot
            if not self.queue_store.put_request(updated, ttl_seconds=int(ttl_seconds)):
                return current
            self._append_state_if_needed(request_id, status="queued")
            return updated
        finally:
            if lock_manager is not None:
                lock_manager.release(lock_handle)

    async def reconcile_pending_terminal_tasks(
        self,
        *,
        task_ids: set[str] | None = None,
        conversation_ids: set[int] | None = None,
        limit: int = 50,
    ) -> None:
        normalized_task_ids = {str(item).strip() for item in set(task_ids or set()) if str(item).strip()}
        normalized_conversation_ids = {
            int(item)
            for item in set(conversation_ids or set())
            if _normalized_positive_int(item) is not None
        }
        processed = 0
        for record in self.queue_store.list_requests():
            if processed >= max(1, int(limit)):
                break
            request_id = str(record.get("request_id") or "").strip()
            if normalized_task_ids and request_id not in normalized_task_ids:
                continue
            conversation_id = _normalized_positive_int(record.get("conversation_id"))
            if normalized_conversation_ids and conversation_id not in normalized_conversation_ids:
                continue
            if self._record_requires_progress_sync(record):
                try:
                    await self._sync_progress_record(record)
                except Exception:
                    logger.warning(
                        "gateway task pending progress reconcile failed request_id=%s",
                        request_id,
                        exc_info=True,
                    )
            if not self._record_requires_terminal_sync(record):
                continue
            try:
                await self._sync_terminal_record(record)
            except Exception:
                logger.warning(
                    "gateway task pending terminal reconcile failed request_id=%s",
                    request_id,
                    exc_info=True,
                )
                continue
            processed += 1

    async def _resolve_route(self, payload: AskRequest):
        resolver = self.app.state.file_context_resolver
        decision_service = self.app.state.route_decision_service
        conversation_file_service = self.app.state.conversation_file_service
        available_files = await conversation_file_service.list_files(
            conversation_id=payload.conversation_id,
            request=self.request,
        )
        file_context = resolver.resolve(
            question=payload.question,
            pdf_context=payload.pdf_context,
            available_files=available_files,
        )
        return decision_service.decide(requested_mode=payload.requested_mode, file_context=file_context), file_context

    def _require_positive_int(self, value: Any, *, detail: str) -> int:
        try:
            normalized = int(value)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=detail) from exc
        if normalized <= 0:
            raise HTTPException(status_code=400, detail=detail)
        return normalized

    def _bind_payload_to_authenticated_user(self, payload: AskRequest, *, auth_context: AuthContext) -> AskRequest:
        requested_user_id = _normalized_positive_int(payload.user_id)
        authenticated_user_id = self._require_positive_int(auth_context.user_id, detail="task_user_id_required")
        if requested_user_id is not None and requested_user_id != authenticated_user_id:
            raise HTTPException(status_code=400, detail="task_user_id_mismatch")
        return payload.model_copy(update={"user_id": authenticated_user_id})

    def _get_owned_task_record(self, task_id: str, *, auth_context: AuthContext) -> dict[str, Any]:
        record = self.queue_store.get_request(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task_not_found")
        owner_user_id = _normalized_positive_int(record.get("user_id"))
        authenticated_user_id = self._require_positive_int(auth_context.user_id, detail="task_user_id_required")
        if owner_user_id is None or owner_user_id != authenticated_user_id:
            raise HTTPException(status_code=404, detail="task_not_found")
        return record

    def _record_requires_terminal_sync(self, record: dict[str, Any]) -> bool:
        public_status = normalize_public_task_status(record.get("status"))
        return public_status in _TERMINAL_SYNCABLE_STATUSES and bool(record.get("terminal_sync_pending"))

    def _record_requires_progress_sync(self, record: dict[str, Any]) -> bool:
        public_status = normalize_public_task_status(record.get("status"))
        return public_status in {"queued", "admitted", "running"} and bool(record.get("progress_sync_pending"))

    async def _sync_progress_record(self, record: dict[str, Any]) -> None:
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            return
        sync_payload = dict(record.get("progress_sync_payload") or {})
        conversation_id = _normalized_positive_int(record.get("conversation_id"))
        user_id = _normalized_positive_int(record.get("user_id"))
        if conversation_id is None or user_id is None:
            return
        await self.app.state.conversation_persistence_service.progress_task_assistant(
            request=self.request,
            conversation_id=conversation_id,
            user_id=user_id,
            task_id=request_id,
            status=str(sync_payload.get("status") or record.get("status") or "running"),
            content_delta=str(sync_payload.get("content_delta") or ""),
            steps=list(sync_payload.get("steps") or []),
            last_seq=max(0, int(sync_payload.get("last_seq") or 0)),
        )
        ttl_seconds = self.queue_store.request_ttl_seconds(request_id) or self._task_ttl_seconds(request_id)
        updated = dict(record)
        updated["persisted_last_seq"] = max(
            int(updated.get("persisted_last_seq") or 0),
            int(sync_payload.get("last_seq") or 0),
        )
        updated["progress_sync_pending"] = False
        updated.pop("progress_sync_payload", None)
        if not self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds))):
            raise RuntimeError("task_progress_sync_clear_failed")

    async def _sync_terminal_record(self, record: dict[str, Any]) -> None:
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            return
        sync_payload = dict(record.get("terminal_sync_payload") or {})
        public_status = normalize_public_task_status(sync_payload.get("terminal_status") or record.get("status"))
        if public_status not in _TERMINAL_SYNCABLE_STATUSES:
            return
        latest_seq = max(
            int(sync_payload.get("last_seq") or 0),
            int(self.relay_store.describe_request(request_id).get("latest_sequence") or 0),
        )
        conversation_id = _normalized_positive_int(record.get("conversation_id"))
        user_id = _normalized_positive_int(record.get("user_id"))
        if conversation_id is not None and user_id is not None:
            await self.app.state.conversation_persistence_service.terminal_task_assistant(
                request=self.request,
                conversation_id=conversation_id,
                user_id=user_id,
                task_id=request_id,
                terminal_status=public_status,
                last_seq=latest_seq,
                answer_text=str(sync_payload.get("answer_text") or ""),
                steps=list(sync_payload.get("steps") or []),
                failure=dict(sync_payload.get("failure") or {}),
                timings=dict(sync_payload.get("timings") or {}),
            )
        quota_result = await self._finalize_quota_grant(
            grant_id=str(record.get("quota_grant_id") or ""),
            success=bool(sync_payload.get("quota_success")),
        )
        if quota_result is not None and not quota_result.success:
            raise RuntimeError("task_terminal_quota_finalize_failed")
        ttl_seconds = self.queue_store.request_ttl_seconds(request_id) or _RELAY_RETENTION_FLOOR_SECONDS
        updated = dict(record)
        updated["persisted_last_seq"] = max(
            int(updated.get("persisted_last_seq") or 0),
            int(latest_seq),
        )
        updated["terminal_sync_pending"] = False
        updated["terminal_synced_at"] = datetime.now(timezone.utc).isoformat()
        updated.pop("terminal_sync_payload", None)
        updated["progress_sync_pending"] = False
        updated.pop("progress_sync_payload", None)
        stored = self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds)))
        if not stored:
            raise RuntimeError("task_terminal_sync_clear_failed")

    def _update_request_record(self, request_id: str, *, updates: dict[str, Any]) -> dict[str, Any] | None:
        current = self.queue_store.get_request(request_id)
        if not isinstance(current, dict):
            return None
        ttl_seconds = self.queue_store.request_ttl_seconds(request_id) or self._task_ttl_seconds(request_id)
        updated = dict(current)
        updated.update(dict(updates or {}))
        if self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds))):
            return updated
        return None

    def _new_progress_accumulator(self, *, persisted_last_seq: int = 0) -> dict[str, Any]:
        return {
            "status": "running",
            "pending_content_delta": "",
            "pending_content_events": 0,
            "observed_last_seq": max(0, int(persisted_last_seq)),
            "persisted_last_seq": max(0, int(persisted_last_seq)),
            "latest_steps": [],
            "dirty": False,
            "last_flush_monotonic": time.monotonic(),
        }

    def _observe_progress_accumulator(
        self,
        accumulator: dict[str, Any],
        *,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        accumulator["status"] = str(status or accumulator.get("status") or "running").strip().lower() or "running"
        accumulator["observed_last_seq"] = max(
            int(accumulator.get("observed_last_seq") or 0),
            max(0, int(last_seq)),
        )
        if steps is not None:
            accumulator["latest_steps"] = list(steps or [])
        delta = str(content_delta or "")
        if delta:
            accumulator["pending_content_delta"] = f"{accumulator.get('pending_content_delta') or ''}{delta}"
            accumulator["pending_content_events"] = int(accumulator.get("pending_content_events") or 0) + 1
        accumulator["dirty"] = True

    def _progress_accumulator_payload(self, accumulator: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(accumulator.get("status") or "running").strip().lower() or "running",
            "last_seq": max(0, int(accumulator.get("observed_last_seq") or 0)),
            "content_delta": str(accumulator.get("pending_content_delta") or ""),
            "steps": list(accumulator.get("latest_steps") or []),
        }

    def _progress_accumulator_should_flush(self, accumulator: dict[str, Any], *, force: bool = False) -> bool:
        if not bool(accumulator.get("dirty")):
            return False
        if force:
            return True
        if int(accumulator.get("pending_content_events") or 0) >= _PROGRESS_FLUSH_MAX_PENDING_EVENTS:
            return True
        return (time.monotonic() - float(accumulator.get("last_flush_monotonic") or 0.0)) >= _PROGRESS_FLUSH_MAX_IDLE_SECONDS

    def _mark_terminal_sync_pending(
        self,
        *,
        record: dict[str, Any],
        terminal_status: str,
        last_seq: int,
        answer_text: str,
        steps: list[dict[str, Any]] | None,
        failure: dict[str, Any] | None,
        quota_success: bool,
    ) -> dict[str, Any] | None:
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            return None
        payload = {
            "terminal_status": normalize_public_task_status(terminal_status),
            "last_seq": max(0, int(last_seq)),
            "answer_text": str(answer_text or ""),
            "steps": list(steps or []),
            "failure": dict(failure or {}),
            "quota_success": bool(quota_success),
        }
        return self._update_request_record(
            request_id,
            updates={
                "terminal_sync_pending": True,
                "terminal_sync_payload": payload,
            },
        )

    async def _abort_live_stream(self, task_id: str) -> None:
        registry = getattr(self.app.state, "active_task_streams", None)
        registry_lock = getattr(self.app.state, "active_task_streams_lock", None)
        entry = None
        if isinstance(registry, dict) and registry_lock is not None:
            with registry_lock:
                entry = registry.pop(str(task_id or "").strip(), None)
        handle = _live_runtime_handle(entry)
        if handle is None:
            return
        owner_loop = _live_runtime_owner_loop(entry)
        try:
            await self._run_live_cleanup(
                entry=entry,
                coroutine_factory=handle.abort,
                label="abort",
                task_id=task_id,
                owner_loop=owner_loop,
            )
        except (asyncio.TimeoutError, Exception):
            logger.warning("gateway task live stream abort failed task_id=%s", task_id, exc_info=True)

    def _get_live_runtime(self, task_id: str) -> Any:
        registry = getattr(self.app.state, "active_task_streams", None)
        registry_lock = getattr(self.app.state, "active_task_streams_lock", None)
        if not isinstance(registry, dict) or registry_lock is None:
            return None
        with registry_lock:
            return registry.get(str(task_id or "").strip())

    async def _flush_live_progress(self, task_id: str, *, entry: Any | None = None) -> None:
        live_entry = entry if entry is not None else self._get_live_runtime(task_id)
        flush_progress = _live_runtime_flush_hook(live_entry)
        if not callable(flush_progress):
            return
        owner_loop = _live_runtime_owner_loop(live_entry)
        if isinstance(live_entry, dict):
            live_entry["cancel_progress_flush_allowed"] = True
        try:
            await self._run_live_cleanup(
                entry=live_entry,
                coroutine_factory=lambda: flush_progress(force=True),
                label="progress_flush",
                task_id=task_id,
                owner_loop=owner_loop,
            )
        except (asyncio.TimeoutError, Exception):
            logger.warning("gateway task live progress flush failed task_id=%s", task_id, exc_info=True)
        finally:
            if isinstance(live_entry, dict):
                live_entry.pop("cancel_progress_flush_allowed", None)

    async def _run_live_cleanup(self, *, entry: Any, coroutine_factory, label: str, task_id: str, owner_loop: Any = None) -> None:
        owner = owner_loop if owner_loop is not None else _live_runtime_owner_loop(entry)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if owner is not None and owner is not current_loop and callable(getattr(owner, "is_running", None)) and owner.is_running():
            future = asyncio.run_coroutine_threadsafe(coroutine_factory(), owner)
            deadline = time.monotonic() + _CANCEL_CLEANUP_TIMEOUT_SECONDS
            while not future.done() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if future.done():
                future.result()
            else:
                future.cancel()
                logger.warning("gateway task live %s timed out task_id=%s", label, task_id)
            return
        await asyncio.wait_for(coroutine_factory(), timeout=_CANCEL_CLEANUP_TIMEOUT_SECONDS)

    def _assert_task_create_admission(self, payload: AskRequest) -> None:
        decision = evaluate_task_create_admission(
            settings=self.settings,
            queue_status_store=self.queue_store,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id,
        )
        if decision.allowed:
            return
        raise HTTPException(status_code=decision.status_code, detail=decision.detail)

    def _assert_requested_mode_enabled(self, payload: AskRequest) -> None:
        _ = payload

    def _assert_route_enabled(self, route_decision: Any) -> None:
        requested_mode = str(getattr(route_decision, "requested_mode", "") or "").strip().lower()
        route_name = str(getattr(route_decision, "route", "") or "").strip().lower()
        if requested_mode == "patent" and route_name in _FILE_ROUTES and not bool(self.settings.patent_file_routes_enabled):
            raise HTTPException(status_code=503, detail="patent_file_route_disabled")

    async def _assert_backend_ready(self, actual_mode: Any) -> None:
        target = self.app.state.backend_registry.get_mode_backend(str(actual_mode or ""))
        probe = await self.app.state.proxy_service.probe_health(target=target)
        if bool(probe.get("ok")):
            return
        raise HTTPException(status_code=503, detail="task_backend_unavailable")

    def _try_fast_dispatch_streaming_task(self, task_id: str, *, auth_context: AuthContext) -> None:
        if not bool(self.settings.admission.enabled and self.settings.admission.dispatcher_enabled):
            return
        record = self._get_owned_task_record(task_id, auth_context=auth_context)
        if normalize_public_task_status(record.get("status")) != "queued":
            return

        dispatcher = ExecutionAdmissionDispatcher(
            settings=self.settings,
            queue_status_store=self.queue_store,
            slot_lease_store=self.slot_lease_store,
        )
        owner_id = f"web-immediate:{os.getpid()}:{task_id}"
        claim = dispatcher.claim_specific_request_if_eligible(
            task_id,
            owner_id=owner_id,
            admitted_at=datetime.now(timezone.utc).isoformat(),
            lease_ttl_seconds=max(30, int(self.settings.admission.poll_interval_seconds) * 4),
            now_epoch=time.time(),
        )
        if claim.outcome != "claimed":
            return

        worker = ExecutionAdmissionWorker(
            dispatcher=dispatcher,
            owner_id=owner_id,
            executor=GatewayTaskExecutor(self.app).execute,
        )

        def _run_claimed_request() -> None:
            try:
                worker.run_claimed_request(claim)
            except Exception:
                logger.exception("gateway immediate task dispatch crashed task_id=%s", task_id)

        threading.Thread(
            target=_run_claimed_request,
            name=f"gateway-immediate-dispatch-{str(task_id or '')[:12]}",
            daemon=True,
        ).start()

    async def _rollback_task_create(
        self,
        *,
        payload: AskRequest,
        task_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> None:
        try:
            await self.app.state.conversation_persistence_service.rollback_task_creation(
                request=self.request,
                conversation_id=payload.conversation_id,
                user_id=payload.user_id,
                task_id=task_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
        except Exception as exc:
            logger.warning("gateway task create rollback failed task_id=%s", task_id, exc_info=True)
            raise HTTPException(status_code=500, detail="task_create_rollback_failed") from exc

    async def _cleanup_failed_task_create(
        self,
        *,
        payload: AskRequest,
        task_id: str,
        user_message_id: str,
        assistant_message_id: str,
        side_effects_started: bool,
        request_record_started: bool,
        quota_grant_id: str,
    ) -> HTTPException | None:
        rollback_error: HTTPException | None = None
        if side_effects_started:
            try:
                await self._rollback_task_create(
                    payload=payload,
                    task_id=task_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                )
            except HTTPException as exc:
                rollback_error = exc
        if request_record_started:
            self._delete_task_create_record(task_id)
        await self._finalize_quota_grant(grant_id=quota_grant_id, success=False)
        return rollback_error

    def _delete_task_create_record(self, task_id: str) -> None:
        try:
            self.queue_store.delete_request(task_id)
        except Exception:
            logger.warning("gateway task create record cleanup failed task_id=%s", task_id, exc_info=True)

    async def _finalize_quota_grant(self, *, grant_id: str, success: bool):
        normalized_grant_id = str(grant_id or "").strip()
        if not normalized_grant_id:
            return None
        return await self.app.state.quota_proxy_service.finalize(
            request=self.request,
            grant_id=normalized_grant_id,
            success=bool(success),
        )

    def _selected_file_ids(self, pdf_context: dict[str, Any] | None) -> list[int]:
        context = dict(pdf_context or {})
        candidates = context.get("selected_ids")
        if not isinstance(candidates, list):
            candidates = context.get("selected_file_ids")
        selected: list[int] = []
        seen: set[int] = set()
        if not isinstance(candidates, list):
            return selected
        for item in candidates:
            try:
                value = int(item)
            except Exception:
                continue
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            selected.append(value)
        return selected

    def _downstream_authorization_header(self) -> str:
        authorization = str(self.request.headers.get("authorization") or "").strip()
        return authorization if authorization else ""

    def _task_ttl_seconds(self, task_id: str) -> int:
        ttl_seconds = self.queue_store.request_ttl_seconds(task_id)
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = int(self.settings.admission.post_admit_attach_ttl_seconds)
        return max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds))

    def _append_state_frame(self, task_id: str, *, status: str) -> dict[str, Any]:
        return self.relay_store.append_frame(
            task_id,
            {"type": "state", "status": str(status or "").strip().lower()},
            ttl_seconds=self._task_ttl_seconds(task_id),
        )

    def _append_state_if_needed(self, task_id: str, *, status: str) -> int:
        frames = self.relay_store.get_frames(task_id, after_sequence=0)
        if frames:
            last_payload = dict(frames[-1].get("payload") or {})
            if (
                str(last_payload.get("type") or "").strip().lower() == "state"
                and normalize_public_task_status(last_payload.get("status")) == normalize_public_task_status(status)
            ):
                return int(frames[-1].get("sequence") or 0)
        appended = self._append_state_frame(task_id, status=status)
        return int(appended.get("sequence") or 0)

    def _build_public_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        task_id = str(record.get("request_id") or "").strip()
        relay_state = self.relay_store.describe_request(task_id)
        public_status = normalize_public_task_status(record.get("status"))
        last_seq = int(relay_state.get("latest_sequence") or 0)
        finished_at = self._terminal_timestamp(record)
        summary = {
            "success": True,
            "task_id": task_id,
            "request_id": task_id,
            "conversation_id": record.get("conversation_id"),
            "assistant_message_id": record.get("assistant_message_id"),
            "status": public_status,
            "requested_mode": record.get("requested_mode"),
            "actual_mode": record.get("actual_mode"),
            "route": record.get("route"),
            "queue_tier": record.get("queue_tier") or self._queue_tier(record.get("actual_mode")),
            "created_at": record.get("created_at"),
            "expires_at": record.get("expires_at"),
            "admitted_at": record.get("admitted_at"),
            "started_at": record.get("started_at"),
            "updated_at": record.get("updated_at") or finished_at or record.get("started_at") or record.get("admitted_at") or record.get("created_at"),
            "finished_at": finished_at,
            "last_seq": last_seq,
            "cancel_allowed": public_status not in _TERMINAL_TASK_STATUSES,
            "replay_available": bool(relay_state.get("frames_tracked")),
            "terminal": public_status in _TERMINAL_TASK_STATUSES,
            "telemetry": _public_task_telemetry(record),
            "error": record.get("failure_reason"),
            "events_url": self._task_url(task_id, "events"),
            "cancel_url": self._task_url(task_id, "cancel"),
        }
        return summary

    def _frame_to_public_event(self, *, summary: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any]:
        payload = dict(frame.get("payload") or {})
        event: dict[str, Any] = {
            "seq": int(frame.get("sequence") or 0),
            "task_id": summary["task_id"],
            "conversation_id": summary.get("conversation_id"),
            "assistant_message_id": summary.get("assistant_message_id"),
        }
        for key, value in payload.items():
            if key in {"seq", "task_id", "conversation_id", "assistant_message_id"}:
                continue
            if key == "status":
                event[key] = normalize_public_task_status(value)
                continue
            event[key] = value
        return event

    def _encode_sse_event(self, payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")

    def _terminal_timestamp(self, record: dict[str, Any]) -> Any:
        for field_name in ("completed_at", "failed_at", "cancelled_at", "canceled_at", "expired_at"):
            value = record.get(field_name)
            if value:
                return value
        return None

    def _queue_tier(self, actual_mode: Any) -> str:
        return "low" if str(actual_mode or "").strip().lower() == "thinking" else "high"

    def _task_url(self, task_id: str, suffix: str) -> str:
        return str(self.request.base_url).rstrip("/") + f"/api/v1/tasks/{task_id}/{suffix}"

    def _release_live_lease(self, task_id: str, *, record: dict[str, Any]) -> bool:
        lease = self.slot_lease_store.get(task_id)
        if not isinstance(lease, dict):
            return True
        owner_id = str(lease.get("owner_id") or record.get("lease_owner_id") or "").strip()
        if not owner_id:
            return False
        released = self.slot_lease_store.release(task_id, owner_id=owner_id)
        if released and self.slot_lease_store.get(task_id) is None:
            return True
        return self.slot_lease_store.get(task_id) is None

    def _cancel_with_conflict_recovery(self, task_id: str, *, cancelled_at: str) -> dict[str, Any] | None:
        cancelled = self.queue_store.cancel_active_request(task_id, cancelled_at=cancelled_at)
        if cancelled is not None:
            return cancelled

        latest = self.queue_store.get_request(task_id)
        if latest is None:
            return None
        if normalize_public_task_status(latest.get("status")) in _TERMINAL_TASK_STATUSES:
            return latest

        retried = self.queue_store.cancel_active_request(task_id, cancelled_at=cancelled_at)
        if retried is not None:
            return retried

        latest = self.queue_store.get_request(task_id)
        if latest is not None and normalize_public_task_status(latest.get("status")) in _TERMINAL_TASK_STATUSES:
            return latest
        return None

    def _assert_no_live_lease(self, task_id: str, *, record: dict[str, Any]) -> None:
        if self._release_live_lease(task_id, record=record):
            return
        raise HTTPException(status_code=500, detail="task_cancel_lease_release_failed")


class GatewayTaskExecutor:
    def __init__(self, app) -> None:
        self.app = app
        self.settings = app.state.settings
        self.queue_store = app.state.execution_queue_status_store
        self.relay_store = app.state.execution_event_relay_store
        self.slot_lease_store = app.state.execution_slot_lease_store
        self.backend_registry = app.state.backend_registry
        self.proxy_service = app.state.proxy_service
        self.conversation_persistence_service = app.state.conversation_persistence_service
        self.quota_proxy_service = app.state.quota_proxy_service

    class GatewayTaskCancelled(Exception):
        pass

    def _new_progress_accumulator(self, *, persisted_last_seq: int = 0) -> dict[str, Any]:
        return {
            "status": "running",
            "pending_content_delta": "",
            "pending_content_events": 0,
            "inflight_content_delta": "",
            "inflight_content_events": 0,
            "inflight_last_seq": max(0, int(persisted_last_seq)),
            "observed_last_seq": max(0, int(persisted_last_seq)),
            "persisted_last_seq": max(0, int(persisted_last_seq)),
            "latest_steps": [],
            "dirty": False,
            "last_flush_monotonic": time.monotonic(),
        }

    def _observe_progress_accumulator(
        self,
        accumulator: dict[str, Any],
        *,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        accumulator["status"] = str(status or accumulator.get("status") or "running").strip().lower() or "running"
        accumulator["observed_last_seq"] = max(
            int(accumulator.get("observed_last_seq") or 0),
            max(0, int(last_seq)),
        )
        if steps is not None:
            accumulator["latest_steps"] = list(steps or [])
        delta = str(content_delta or "")
        if delta:
            accumulator["pending_content_delta"] = f"{accumulator.get('pending_content_delta') or ''}{delta}"
            accumulator["pending_content_events"] = int(accumulator.get("pending_content_events") or 0) + 1
        accumulator["dirty"] = True

    def _progress_accumulator_payload(self, accumulator: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(accumulator.get("status") or "running").strip().lower() or "running",
            "last_seq": max(0, int(accumulator.get("observed_last_seq") or 0)),
            "content_delta": str(accumulator.get("pending_content_delta") or ""),
            "steps": list(accumulator.get("latest_steps") or []),
        }

    def _begin_progress_accumulator_flush(self, accumulator: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
        if int(accumulator.get("inflight_content_events") or 0) > 0 or str(accumulator.get("inflight_content_delta") or ""):
            return None
        if not self._progress_accumulator_should_flush(accumulator, force=force):
            return None
        payload = self._progress_accumulator_payload(accumulator)
        accumulator["inflight_content_delta"] = str(accumulator.get("pending_content_delta") or "")
        accumulator["inflight_content_events"] = int(accumulator.get("pending_content_events") or 0)
        accumulator["inflight_last_seq"] = max(0, int(payload.get("last_seq") or 0))
        accumulator["pending_content_delta"] = ""
        accumulator["pending_content_events"] = 0
        accumulator["dirty"] = False
        return payload

    def _finish_progress_accumulator_flush(self, accumulator: dict[str, Any], *, persisted_last_seq: int, success: bool) -> None:
        accumulator["persisted_last_seq"] = max(
            int(accumulator.get("persisted_last_seq") or 0),
            max(0, int(persisted_last_seq)),
        )
        inflight_content_delta = str(accumulator.get("inflight_content_delta") or "")
        inflight_content_events = int(accumulator.get("inflight_content_events") or 0)
        if success:
            accumulator["last_flush_monotonic"] = time.monotonic()
        else:
            accumulator["pending_content_delta"] = f"{inflight_content_delta}{accumulator.get('pending_content_delta') or ''}"
            accumulator["pending_content_events"] = inflight_content_events + int(accumulator.get("pending_content_events") or 0)
            accumulator["dirty"] = True
        accumulator["inflight_content_delta"] = ""
        accumulator["inflight_content_events"] = 0
        accumulator["inflight_last_seq"] = max(0, int(accumulator.get("persisted_last_seq") or 0))

    def _progress_accumulator_should_flush(self, accumulator: dict[str, Any], *, force: bool = False) -> bool:
        if not bool(accumulator.get("dirty")):
            return False
        if force:
            return True
        if int(accumulator.get("pending_content_events") or 0) >= _PROGRESS_FLUSH_MAX_PENDING_EVENTS:
            return True
        return (time.monotonic() - float(accumulator.get("last_flush_monotonic") or 0.0)) >= _PROGRESS_FLUSH_MAX_IDLE_SECONDS

    def _runtime_observe_progress(
        self,
        runtime: dict[str, Any],
        *,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        runtime_lock = runtime["lock"]
        with runtime_lock:
            self._observe_progress_accumulator(
                runtime["progress_accumulator"],
                status=status,
                last_seq=last_seq,
                content_delta=content_delta,
                steps=steps,
            )
            runtime["latest_steps"] = list(runtime["progress_accumulator"].get("latest_steps") or [])
            runtime["latest_observed_seq"] = max(
                int(runtime.get("latest_observed_seq") or 0),
                max(0, int(last_seq)),
            )
            delta = str(content_delta or "")
            if delta:
                runtime["answer_text"] = f"{runtime.get('answer_text') or ''}{delta}"

    def _reconcile_runtime_progress_after_persist(self, runtime: dict[str, Any], *, success: bool, flushed_last_seq: int) -> None:
        runtime_lock = runtime["lock"]
        with runtime_lock:
            accumulator = runtime["progress_accumulator"]
            request = runtime["request"]
            persisted_last_seq = int(
                request.get("persisted_last_seq")
                or accumulator.get("persisted_last_seq")
                or 0
            )
            self._finish_progress_accumulator_flush(
                accumulator,
                persisted_last_seq=(
                    max(persisted_last_seq, max(0, int(flushed_last_seq)))
                    if success
                    else max(0, int(persisted_last_seq))
                ),
                success=success,
            )
            runtime["progress_flush_task"] = None

    async def _flush_runtime_progress(self, runtime: dict[str, Any], *, force: bool = False) -> bool:
        while True:
            runtime_lock = runtime["lock"]
            with runtime_lock:
                cancel_event = runtime.get("cancel_event")
                allow_cancelled_flush = bool(runtime.get("cancel_progress_flush_allowed"))
                if (
                    cancel_event is not None
                    and getattr(cancel_event, "is_set", lambda: False)()
                    and not allow_cancelled_flush
                ):
                    return False
                inflight_task = runtime.get("progress_flush_task")
                if inflight_task is not None and not inflight_task.done():
                    wait_for = inflight_task
                else:
                    wait_for = None
                    accumulator = runtime["progress_accumulator"]
                    payload = self._begin_progress_accumulator_flush(accumulator, force=force)
                    if payload is None:
                        return False
                    request = runtime["request"]
                    internal_request = runtime["internal_request"]
                    flushed_last_seq = max(0, int(payload.get("last_seq") or 0))
                    request_id = str(request.get("request_id") or "").strip()

                    async def _persist_progress() -> bool:
                        success = False
                        try:
                            await self._sync_progress_best_effort(
                                request=request,
                                internal_request=internal_request,
                                **payload,
                            )
                            persisted_last_seq = max(0, int(request.get("persisted_last_seq") or 0))
                            success = persisted_last_seq >= flushed_last_seq
                        except Exception:
                            logger.warning(
                                "gateway task progress flush crashed request_id=%s",
                                request_id,
                                exc_info=True,
                            )
                        finally:
                            self._reconcile_runtime_progress_after_persist(
                                runtime,
                                success=success,
                                flushed_last_seq=flushed_last_seq,
                            )
                        return success

                    wait_for = asyncio.create_task(_persist_progress())
                    runtime["progress_flush_task"] = wait_for
            if inflight_task is not None and not inflight_task.done():
                if not force:
                    return False
                if not await self._await_progress_flush_or_cancel(
                    wait_for,
                    cancel_event=cancel_event,
                    allow_cancelled_flush=allow_cancelled_flush,
                ):
                    return False
                continue
            return await self._await_progress_flush_or_cancel(
                wait_for,
                cancel_event=cancel_event,
                allow_cancelled_flush=allow_cancelled_flush,
            )

    async def _await_progress_flush_or_cancel(self, wait_for: asyncio.Task, *, cancel_event: Any, allow_cancelled_flush: bool = False) -> bool:
        while not wait_for.done():
            if (
                cancel_event is not None
                and getattr(cancel_event, "is_set", lambda: False)()
                and not allow_cancelled_flush
            ):
                return False
            await asyncio.sleep(0.05)
        return bool(await wait_for)

    def execute(self, request: dict[str, Any], lease: dict[str, Any], renew_lease=None):
        return anyio.run(self._execute_async, request or {}, lease or {}, renew_lease)

    def _persist_request_telemetry(self, request: dict[str, Any], updates: dict[str, Any] | None = None, **fields: Any) -> dict[str, int]:
        request_id = str(request.get("request_id") or "").strip()
        merged_request_telemetry = _merge_task_telemetry(
            request.get("telemetry"),
            dict(updates or {}),
            fields,
        )
        request["telemetry"] = merged_request_telemetry
        if not request_id or not merged_request_telemetry:
            return merged_request_telemetry
        current = self.queue_store.get_request(request_id)
        if not isinstance(current, dict):
            return merged_request_telemetry
        ttl_seconds = self.queue_store.request_ttl_seconds(request_id)
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = int(self.settings.admission.post_admit_attach_ttl_seconds)
        updated = dict(current)
        updated["telemetry"] = _merge_task_telemetry(
            current.get("telemetry"),
            merged_request_telemetry,
        )
        if self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds))):
            request["telemetry"] = dict(updated.get("telemetry") or {})
        return dict(request.get("telemetry") or {})

    async def _await_with_cancel(self, awaitable, *, cancel_event: threading.Event, request_id: str, label: str):
        task = asyncio.create_task(awaitable)

        def _cleanup_done(done_task: asyncio.Task) -> None:
            if done_task.cancelled():
                return
            try:
                result = done_task.result()
            except BaseException:
                return
            abort = getattr(result, "abort", None)
            if not callable(abort):
                return
            try:
                asyncio.create_task(abort())
            except RuntimeError:
                logger.warning("gateway task cancelled %s cleanup could not schedule abort request_id=%s", label, request_id)

        while not task.done():
            if cancel_event.is_set():
                task.add_done_callback(_cleanup_done)
                task.cancel()
                raise self.GatewayTaskCancelled(label)
            await asyncio.sleep(0.05)
        return await task

    async def _next_chunk_with_cancel(self, iterator, *, handle: Any, cancel_event: threading.Event, request_id: str):
        task = asyncio.create_task(anext(iterator))
        while not task.done():
            if cancel_event.is_set():
                try:
                    await asyncio.wait_for(handle.abort(), timeout=_CANCEL_CLEANUP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning("gateway task read abort timed out request_id=%s", request_id)
                finally:
                    task.cancel()
                raise self.GatewayTaskCancelled("read")
            await asyncio.sleep(0.05)
        return await task

    def _cancelled_execution_outcome(self, request_id: str) -> AdmissionExecutionOutcome:
        return self._terminalized_execution_outcome(request_id) or AdmissionExecutionOutcome(
            outcome="completed",
            reason="task_cancelled",
            terminal_status="cancelled",
        )

    async def _execute_async(self, request: dict[str, Any], lease: dict[str, Any], renew_lease=None):
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            return AdmissionExecutionOutcome(outcome="failed", reason="task_request_id_missing", terminal_status="failed")
        execute_started = time.perf_counter()
        internal_request = self._build_internal_request(
            trace_id=request_id,
            actual_mode=request.get("actual_mode"),
            downstream_authorization=request.get("downstream_authorization")
            or (request.get("execution_snapshot") or {}).get("downstream_authorization"),
            patent_stream_capability=_resolve_task_patent_stream_capability(request),
        )
        dispatcher = ExecutionAdmissionDispatcher(
            settings=self.settings,
            queue_status_store=self.queue_store,
            slot_lease_store=self.slot_lease_store,
        )
        admitted_seq = self._append_state_if_needed(request_id, status="admitted")
        await self._sync_progress_best_effort(
            request=request,
            internal_request=internal_request,
            status="admitted",
            last_seq=admitted_seq,
        )
        running = dispatcher.transition_to_running(
            request_id,
            owner_id=str(lease.get("owner_id") or request.get("lease_owner_id") or "").strip(),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        if running.outcome not in {"running"}:
            return AdmissionExecutionOutcome(outcome="failed", reason=f"task_running_transition_failed:{running.outcome}", terminal_status="failed")
        self._persist_request_telemetry(request, dispatch_started_at_ms=_epoch_ms())
        running_seq = self._append_state_if_needed(request_id, status="running")

        target = self.backend_registry.get(str(request.get("actual_mode") or ""))
        path = f"/api/{str(request.get('actual_mode') or '').strip()}/ask_stream"
        progress_accumulator = self._new_progress_accumulator(
            persisted_last_seq=int(request.get("persisted_last_seq") or 0),
        )
        live_runtime = {
            "handle": None,
            "cancel_event": threading.Event(),
            "owner_loop": asyncio.get_running_loop(),
            "lock": threading.RLock(),
            "request": request,
            "internal_request": internal_request,
            "progress_accumulator": progress_accumulator,
            "progress_flush_task": None,
            "answer_text": "",
            "latest_steps": [],
            "latest_observed_seq": 0,
        }

        async def _flush_live_progress(*, force: bool = False) -> bool:
            return await self._flush_runtime_progress(live_runtime, force=force)

        live_runtime["flush_progress"] = _flush_live_progress
        self._register_live_handle(request_id, live_runtime)
        cancel_event = live_runtime["cancel_event"]
        terminalized = self._terminalized_execution_outcome(request_id)
        if terminalized is not None:
            cancel_event.set()
            self._unregister_live_handle(request_id)
            return terminalized
        try:
            handle = await self._await_with_cancel(
                self.proxy_service.open_json_stream(
                    request=internal_request,
                    target=target,
                    path=path,
                    payload=self._upstream_payload(request),
                ),
                cancel_event=cancel_event,
                request_id=request_id,
                label="open",
            )
        except self.GatewayTaskCancelled:
            self._unregister_live_handle(request_id)
            return self._cancelled_execution_outcome(request_id)
        except Exception as exc:
            self._unregister_live_handle(request_id)
            await self._terminalize_failure(
                request=request,
                internal_request=internal_request,
                last_seq=max(admitted_seq, running_seq),
                reason=str(exc) or "upstream_stream_unavailable",
            )
            return AdmissionExecutionOutcome(outcome="failed", reason=str(exc) or "upstream_stream_unavailable", terminal_status="failed")

        if handle.status_code >= 400 and "text/event-stream" not in str(handle.headers.get("content-type") or ""):
            body = await handle.upstream.aread()
            await handle.upstream.aclose()
            await handle.client.aclose()
            reason = body.decode("utf-8", errors="ignore") or "upstream_error"
            self._unregister_live_handle(request_id)
            await self._terminalize_failure(
                request=request,
                internal_request=internal_request,
                last_seq=max(admitted_seq, running_seq),
                reason=reason,
            )
            return AdmissionExecutionOutcome(outcome="failed", reason=reason, terminal_status="failed")
        _log_task_event(
            "task upstream stream opened",
            request_id=request_id,
            conversation_id=request.get("conversation_id"),
            actual_mode=request.get("actual_mode"),
            route=request.get("route"),
            backend=handle.backend,
            status_code=handle.status_code,
            elapsed_ms=round((time.perf_counter() - execute_started) * 1000, 3),
        )
        live_runtime["handle"] = handle
        idle_flush_stop = asyncio.Event()

        async def _idle_flush_loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(idle_flush_stop.wait(), timeout=_PROGRESS_FLUSH_MAX_IDLE_SECONDS)
                    return
                except asyncio.TimeoutError:
                    try:
                        await self._flush_runtime_progress(live_runtime)
                    except Exception:
                        logger.warning("gateway task idle progress flush failed request_id=%s", request_id, exc_info=True)

        idle_flush_task = asyncio.create_task(_idle_flush_loop())
        frame_buffer = SSEFrameBuffer()
        content_parts: list[str] = []
        step_order: list[str] = []
        step_map: dict[str, dict[str, Any]] = {}
        thinking_count = 0
        first_step_logged = False
        first_content_logged = False
        latest_seq = max(admitted_seq, running_seq)
        self._runtime_observe_progress(
            live_runtime,
            status="running",
            last_seq=running_seq,
            steps=[step_map[key] for key in step_order],
        )
        await self._flush_runtime_progress(live_runtime, force=True)
        try:
            iterator = handle.body_iter().__aiter__()
            while True:
                try:
                    chunk = await self._next_chunk_with_cancel(
                        iterator,
                        handle=handle,
                        cancel_event=cancel_event,
                        request_id=request_id,
                    )
                except StopAsyncIteration:
                    break
                except self.GatewayTaskCancelled:
                    try:
                        await asyncio.wait_for(handle.abort(), timeout=_CANCEL_CLEANUP_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        logger.warning("gateway task cancelled stream abort timed out request_id=%s", request_id)
                    return self._cancelled_execution_outcome(request_id)
                terminalized = self._terminalized_execution_outcome(request_id)
                if terminalized is not None:
                    return terminalized
                if callable(renew_lease):
                    try:
                        renew_lease()
                    except Exception:
                        logger.warning("gateway task lease renew failed request_id=%s", request_id, exc_info=True)
                for frame in frame_buffer.feed(chunk):
                    terminalized = self._terminalized_execution_outcome(request_id)
                    if terminalized is not None:
                        return terminalized
                    payload, _prefix_lines = parse_sse_json_frame(frame)
                    if not isinstance(payload, dict):
                        continue
                    event_type = str(payload.get("type") or "").strip().lower()
                    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                    telemetry = metadata.get("telemetry") if isinstance(metadata.get("telemetry"), dict) else {}
                    merged_telemetry = _merge_task_telemetry(request.get("telemetry"))
                    if telemetry:
                        merged_telemetry = self._persist_request_telemetry(request, updates=telemetry)
                    if event_type == "metadata":
                        if _normalized_epoch_ms(merged_telemetry.get("backend_stream_opened_at_ms")) is None:
                            self._persist_request_telemetry(request, backend_stream_opened_at_ms=_epoch_ms())
                        if not (
                            isinstance(payload.get("stage_timings_ms"), dict)
                            or isinstance(payload.get("timings"), dict)
                        ):
                            continue
                    appended = self.relay_store.append_frame(
                        request_id,
                        payload,
                        ttl_seconds=self._task_ttl_seconds(request_id),
                    )
                    latest_seq = int(appended.get("sequence") or latest_seq)
                    if bool(appended.get("ignored")):
                        continue
                    terminalized = self._terminalized_execution_outcome(request_id)
                    if terminalized is not None:
                        return terminalized
                    if event_type == "thinking":
                        if _normalized_epoch_ms((request.get("telemetry") or {}).get("first_step_at_ms")) is None:
                            self._persist_request_telemetry(request, first_step_at_ms=_epoch_ms())
                        if not first_step_logged:
                            first_step_logged = True
                            _log_task_event(
                                "task first step",
                                request_id=request_id,
                                conversation_id=request.get("conversation_id"),
                                actual_mode=request.get("actual_mode"),
                                route=request.get("route"),
                                step=f"thinking_{thinking_count + 1}",
                                elapsed_ms=round((time.perf_counter() - execute_started) * 1000, 3),
                            )
                        thinking_count += 1
                        step_key = f"thinking_{thinking_count}"
                        self._upsert_step(
                            step_order=step_order,
                            step_map=step_map,
                            step_key=step_key,
                            payload={
                                "step": step_key,
                                "title": "",
                                "message": str(payload.get("content") or payload.get("message") or step_key).strip() or step_key,
                                "status": "success",
                                "data": {},
                            },
                        )
                        self._runtime_observe_progress(
                            live_runtime,
                            status="running",
                            last_seq=latest_seq,
                            steps=[step_map[key] for key in step_order],
                        )
                        await self._flush_runtime_progress(live_runtime, force=True)
                        continue
                    if event_type == "step":
                        if _normalized_epoch_ms((request.get("telemetry") or {}).get("first_step_at_ms")) is None:
                            self._persist_request_telemetry(request, first_step_at_ms=_epoch_ms())
                        if not first_step_logged:
                            first_step_logged = True
                            _log_task_event(
                                "task first step",
                                request_id=request_id,
                                conversation_id=request.get("conversation_id"),
                                actual_mode=request.get("actual_mode"),
                                route=request.get("route"),
                                step=str(payload.get("step") or ""),
                                elapsed_ms=round((time.perf_counter() - execute_started) * 1000, 3),
                            )
                        step_key = str(payload.get("step") or f"step_{len(step_order) + 1}").strip() or f"step_{len(step_order) + 1}"
                        self._upsert_step(
                            step_order=step_order,
                            step_map=step_map,
                            step_key=step_key,
                            payload={
                                "step": step_key,
                                "title": str(payload.get("title") or "").strip(),
                                "message": str(payload.get("message") or payload.get("content") or step_key).strip() or step_key,
                                "status": str(payload.get("status") or "processing").strip() or "processing",
                                "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
                            },
                        )
                        self._runtime_observe_progress(
                            live_runtime,
                            status="running",
                            last_seq=latest_seq,
                            steps=[step_map[key] for key in step_order],
                        )
                        await self._flush_runtime_progress(live_runtime, force=True)
                        continue
                    if event_type == "content":
                        if _normalized_epoch_ms((request.get("telemetry") or {}).get("first_content_at_ms")) is None:
                            self._persist_request_telemetry(request, first_content_at_ms=_epoch_ms())
                        delta = str(payload.get("content") or payload.get("delta") or "")
                        if delta and not first_content_logged:
                            first_content_logged = True
                            _log_task_event(
                                "task first content",
                                request_id=request_id,
                                conversation_id=request.get("conversation_id"),
                                actual_mode=request.get("actual_mode"),
                                route=request.get("route"),
                                content_chars=len(delta),
                                elapsed_ms=round((time.perf_counter() - execute_started) * 1000, 3),
                            )
                        if _task_content_persists_in_main_body(payload):
                            if delta:
                                content_parts.append(delta)
                            self._runtime_observe_progress(
                                live_runtime,
                                status="running",
                                last_seq=latest_seq,
                                content_delta=delta,
                                steps=[step_map[key] for key in step_order],
                            )
                            await self._flush_runtime_progress(live_runtime)
                        continue
                    if event_type == "done":
                        terminalized = self._terminalized_execution_outcome(request_id)
                        if terminalized is not None:
                            return terminalized
                        if cancel_event.is_set():
                            return self._cancelled_execution_outcome(request_id)
                        answer_text = str(payload.get("final_answer") or "".join(content_parts)).strip()
                        done_timings = payload.get("timings")
                        if not isinstance(done_timings, dict):
                            done_timings = {}
                        self._runtime_observe_progress(
                            live_runtime,
                            status="running",
                            last_seq=max(0, int(latest_seq - 1)),
                            steps=[step_map[key] for key in step_order],
                        )
                        await self._flush_runtime_progress(live_runtime, force=True)
                        if cancel_event.is_set():
                            return self._cancelled_execution_outcome(request_id)
                        try:
                            await self.conversation_persistence_service.terminal_task_assistant(
                                request=internal_request,
                                conversation_id=request.get("conversation_id"),
                                user_id=request.get("user_id"),
                                task_id=request_id,
                                terminal_status="completed",
                                last_seq=latest_seq,
                                answer_text=answer_text,
                                steps=[step_map[key] for key in step_order],
                                failure={},
                                timings=dict(done_timings),
                            )
                        except Exception:
                            logger.warning("gateway task terminal write failed after done request_id=%s", request_id, exc_info=True)
                            self._queue_terminal_sync_update(
                                request=request,
                                terminal_status="completed",
                                last_seq=latest_seq,
                                answer_text=answer_text,
                                steps=[step_map[key] for key in step_order],
                                failure={},
                                timings=dict(done_timings),
                                quota_success=True,
                            )
                            return AdmissionExecutionOutcome(
                                outcome="completed",
                                terminal_status="completed",
                                result_payload={"final_answer": answer_text},
                            )
                        request["persisted_last_seq"] = max(int(request.get("persisted_last_seq") or 0), int(latest_seq))
                        self._clear_progress_sync_pending(request_id, persisted_last_seq=latest_seq)
                        if cancel_event.is_set():
                            return self._cancelled_execution_outcome(request_id)
                        quota_result = await self._finalize_quota(
                            internal_request=internal_request,
                            grant_id=request.get("quota_grant_id"),
                            success=True,
                        )
                        if quota_result is not None and not quota_result.success:
                            logger.warning("gateway task quota finalize failed after done request_id=%s", request_id)
                            self._queue_terminal_sync_update(
                                request=request,
                                terminal_status="completed",
                                last_seq=latest_seq,
                                answer_text=answer_text,
                                steps=[step_map[key] for key in step_order],
                                failure={},
                                timings=dict(done_timings),
                                quota_success=True,
                            )
                        return AdmissionExecutionOutcome(
                            outcome="completed",
                            terminal_status="completed",
                            result_payload={"final_answer": answer_text},
                        )
                    if event_type == "error":
                        reason = str(payload.get("message") or payload.get("error") or "upstream_error")
                        self._runtime_observe_progress(
                            live_runtime,
                            status="running",
                            last_seq=max(0, int(latest_seq - 1)),
                            steps=[step_map[key] for key in step_order],
                        )
                        await self._flush_runtime_progress(live_runtime, force=True)
                        await self._terminalize_failure(
                            request=request,
                            internal_request=internal_request,
                            last_seq=latest_seq,
                            reason=reason,
                            answer_text="".join(content_parts),
                            steps=[step_map[key] for key in step_order],
                        )
                        return AdmissionExecutionOutcome(outcome="failed", reason=reason, terminal_status="failed")
        finally:
            idle_flush_stop.set()
            idle_flush_task.cancel()
            with suppress(asyncio.CancelledError):
                await idle_flush_task
            if cancel_event.is_set():
                try:
                    await asyncio.wait_for(handle.abort(), timeout=_CANCEL_CLEANUP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning("gateway task cancelled stream abort timed out request_id=%s", request_id)
                except Exception:
                    logger.warning("gateway task cancelled stream abort failed request_id=%s", request_id, exc_info=True)
            self._unregister_live_handle(request_id)

        terminalized = self._terminalized_execution_outcome(request_id)
        if terminalized is not None:
            return terminalized
        await self._terminalize_failure(
            request=request,
            internal_request=internal_request,
            last_seq=latest_seq,
            reason="stream_ended_without_done",
            answer_text="".join(content_parts),
            steps=[step_map[key] for key in step_order],
        )
        return AdmissionExecutionOutcome(outcome="failed", reason="stream_ended_without_done", terminal_status="failed")

    def _build_internal_request(
        self,
        *,
        trace_id: str,
        actual_mode: Any,
        downstream_authorization: Any = None,
        patent_stream_capability: Any = None,
    ) -> Request:
        internal_token = self.conversation_persistence_service._internal_token()
        headers = [
            (b"accept", b"text/event-stream"),
            (b"content-type", b"application/json"),
            (b"x-gateway-task-execution", b"1"),
            (b"x-gateway-owned-persistence", b"1"),
            (b"x-internal-service-name", b"gateway"),
            (b"x-internal-service-token", str(internal_token or "").encode("utf-8")),
        ]
        authorization = str(downstream_authorization or "").strip()
        if authorization:
            headers.append((b"authorization", authorization.encode("utf-8")))
        stream_capability = _normalized_patent_stream_capability(patent_stream_capability)
        if stream_capability:
            headers.append((_PATENT_STREAM_CAPABILITY_HEADER, stream_capability.encode("utf-8")))
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": f"/api/{str(actual_mode or '').strip()}/ask_stream",
            "raw_path": f"/api/{str(actual_mode or '').strip()}/ask_stream".encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 0),
            "server": ("testserver", 80),
            "state": {"trace_id": str(trace_id or "").strip()},
            "app": self.app,
        }

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        return Request(scope, receive=_receive)

    def _upstream_payload(self, request: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(request.get("execution_snapshot") or {})
        snapshot.setdefault("question", request.get("question"))
        snapshot.setdefault("conversation_id", request.get("conversation_id"))
        snapshot.setdefault("user_id", request.get("user_id"))
        snapshot.setdefault("requested_mode", request.get("requested_mode"))
        snapshot.setdefault("actual_mode", request.get("actual_mode"))
        snapshot.setdefault("route", request.get("route"))
        snapshot.setdefault("trace_id", str(request.get("request_id") or ""))
        snapshot.setdefault("chat_history", [])
        snapshot.setdefault("options", {})
        return snapshot

    async def _sync_progress(
        self,
        *,
        request: dict[str, Any],
        internal_request: Request,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        await self.conversation_persistence_service.progress_task_assistant(
            request=internal_request,
            conversation_id=request.get("conversation_id"),
            user_id=request.get("user_id"),
            task_id=str(request.get("request_id") or ""),
            status=status,
            content_delta=content_delta,
            steps=list(steps or []),
            last_seq=last_seq,
        )

    async def _sync_progress_best_effort(
        self,
        *,
        request: dict[str, Any],
        internal_request: Request,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
        ) -> None:
        try:
            await self._sync_progress(
                request=request,
                internal_request=internal_request,
                status=status,
                last_seq=last_seq,
                content_delta=content_delta,
                steps=steps,
            )
            request["persisted_last_seq"] = max(
                int(request.get("persisted_last_seq") or 0),
                max(0, int(last_seq)),
            )
            self._clear_progress_sync_pending(
                str(request.get("request_id") or "").strip(),
                persisted_last_seq=max(0, int(last_seq)),
            )
        except Exception:
            request_id = str(request.get("request_id") or "").strip()
            logger.warning("gateway task progress sync failed request_id=%s", request_id, exc_info=True)
            self._mark_progress_sync_pending(
                request_id,
                status=status,
                last_seq=last_seq,
                content_delta=content_delta,
                steps=steps,
            )

    async def _terminalize_failure(
        self,
        *,
        request: dict[str, Any],
        internal_request: Request,
        last_seq: int,
        reason: str,
        answer_text: str = "",
        steps: list[dict[str, Any]] | None = None,
        ) -> None:
        failure_payload = {"message": str(reason or "execution_failed"), "error": str(reason or "execution_failed")}
        terminal_write_succeeded = False
        try:
            await self.conversation_persistence_service.terminal_task_assistant(
                request=internal_request,
                conversation_id=request.get("conversation_id"),
                user_id=request.get("user_id"),
                task_id=str(request.get("request_id") or ""),
                terminal_status="failed",
                last_seq=max(0, int(last_seq)),
                answer_text=str(answer_text or ""),
                steps=list(steps or []),
                failure=failure_payload,
            )
            terminal_write_succeeded = True
        except Exception:
            logger.warning(
                "gateway task terminal failure write failed request_id=%s",
                str(request.get("request_id") or ""),
                exc_info=True,
            )
            self._queue_terminal_sync_update(
                request=request,
                terminal_status="failed",
                last_seq=max(0, int(last_seq)),
                answer_text=str(answer_text or ""),
                steps=list(steps or []),
                failure=failure_payload,
                quota_success=False,
            )
        quota_result = await self._finalize_quota(internal_request=internal_request, grant_id=request.get("quota_grant_id"), success=False)
        if quota_result is not None and not quota_result.success:
            self._queue_terminal_sync_update(
                request=request,
                terminal_status="failed",
                last_seq=max(0, int(last_seq)),
                answer_text=str(answer_text or ""),
                steps=list(steps or []),
                failure=failure_payload,
                quota_success=False,
            )
        if terminal_write_succeeded:
            request["persisted_last_seq"] = max(
                int(request.get("persisted_last_seq") or 0),
                max(0, int(last_seq)),
            )
            self._clear_progress_sync_pending(
                str(request.get("request_id") or "").strip(),
                persisted_last_seq=max(0, int(last_seq)),
            )

    async def _finalize_quota(self, *, internal_request: Request, grant_id: Any, success: bool):
        normalized_grant_id = str(grant_id or "").strip()
        if not normalized_grant_id:
            return None
        return await self.quota_proxy_service.finalize(
            request=internal_request,
            grant_id=normalized_grant_id,
            success=bool(success),
        )

    def _queue_terminal_sync_update(
        self,
        *,
        request: dict[str, Any],
        terminal_status: str,
        last_seq: int,
        answer_text: str,
        steps: list[dict[str, Any]] | None,
        failure: dict[str, Any] | None,
        quota_success: bool,
        timings: dict[str, Any] | None = None,
    ) -> None:
        request["post_complete_record_updates"] = {
            "terminal_sync_pending": True,
            "terminal_sync_payload": {
                "terminal_status": normalize_public_task_status(terminal_status),
                "last_seq": max(0, int(last_seq)),
                "answer_text": str(answer_text or ""),
                "steps": list(steps or []),
                "failure": dict(failure or {}),
                "timings": dict(timings or {}),
                "quota_success": bool(quota_success),
            },
        }

    def _mark_progress_sync_pending(
        self,
        request_id: str,
        *,
        status: str,
        last_seq: int,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return
        record = self.queue_store.get_request(normalized_id)
        if not isinstance(record, dict):
            return
        ttl_seconds = self.queue_store.request_ttl_seconds(normalized_id) or self._task_ttl_seconds(normalized_id)
        updated = dict(record)
        updated["progress_sync_pending"] = True
        updated["progress_sync_payload"] = {
            "status": str(status or "").strip().lower(),
            "last_seq": max(0, int(last_seq)),
            "content_delta": str(content_delta or ""),
            "steps": list(steps or []),
        }
        self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds)))

    def _clear_progress_sync_pending(self, request_id: str, *, persisted_last_seq: int | None = None) -> None:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return
        record = self.queue_store.get_request(normalized_id)
        if not isinstance(record, dict):
            return
        if (
            not record.get("progress_sync_pending")
            and "progress_sync_payload" not in record
            and persisted_last_seq is None
        ):
            return
        ttl_seconds = self.queue_store.request_ttl_seconds(normalized_id) or self._task_ttl_seconds(normalized_id)
        updated = dict(record)
        if persisted_last_seq is not None:
            updated["persisted_last_seq"] = max(
                int(updated.get("persisted_last_seq") or 0),
                max(0, int(persisted_last_seq)),
            )
        updated["progress_sync_pending"] = False
        updated.pop("progress_sync_payload", None)
        self.queue_store.put_request(updated, ttl_seconds=max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds)))

    def _append_state_if_needed(self, request_id: str, *, status: str) -> int:
        frames = self.relay_store.get_frames(request_id, after_sequence=0)
        if frames:
            last_payload = dict(frames[-1].get("payload") or {})
            if (
                str(last_payload.get("type") or "").strip().lower() == "state"
                and normalize_public_task_status(last_payload.get("status")) == normalize_public_task_status(status)
            ):
                return int(frames[-1].get("sequence") or 0)
        appended = self.relay_store.append_frame(
            request_id,
            {"type": "state", "status": str(status or "").strip().lower()},
            ttl_seconds=self._task_ttl_seconds(request_id),
        )
        return int(appended.get("sequence") or 0)

    def _task_ttl_seconds(self, request_id: str) -> int:
        ttl_seconds = self.queue_store.request_ttl_seconds(request_id)
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = int(self.settings.admission.post_admit_attach_ttl_seconds)
        return max(_RELAY_RETENTION_FLOOR_SECONDS, int(ttl_seconds))

    def _upsert_step(
        self,
        *,
        step_order: list[str],
        step_map: dict[str, dict[str, Any]],
        step_key: str,
        payload: dict[str, Any],
    ) -> None:
        if step_key not in step_map:
            step_order.append(step_key)
            step_map[step_key] = payload
            return
        step_map[step_key] = {**step_map[step_key], **payload}

    def _register_live_handle(self, request_id: str, handle) -> None:
        registry = getattr(self.app.state, "active_task_streams", None)
        registry_lock = getattr(self.app.state, "active_task_streams_lock", None)
        if not isinstance(registry, dict) or registry_lock is None:
            return
        with registry_lock:
            registry[str(request_id or "").strip()] = handle

    def _unregister_live_handle(self, request_id: str) -> None:
        registry = getattr(self.app.state, "active_task_streams", None)
        registry_lock = getattr(self.app.state, "active_task_streams_lock", None)
        if not isinstance(registry, dict) or registry_lock is None:
            return
        with registry_lock:
            registry.pop(str(request_id or "").strip(), None)

    def _terminalized_execution_outcome(self, request_id: str) -> AdmissionExecutionOutcome | None:
        record = self.queue_store.get_request(request_id)
        if not isinstance(record, dict):
            return None
        public_status = normalize_public_task_status(record.get("status"))
        if public_status not in _TERMINAL_TASK_STATUSES:
            return None
        if public_status == "failed":
            return AdmissionExecutionOutcome(
                outcome="failed",
                reason=str(record.get("failure_reason") or "task_terminalized"),
                terminal_status="failed",
            )
        if public_status == "expired":
            return AdmissionExecutionOutcome(
                outcome="completed",
                reason="task_terminalized",
                terminal_status="expired",
            )
        if public_status == "completed":
            return AdmissionExecutionOutcome(
                outcome="completed",
                reason="task_terminalized",
                terminal_status="completed",
            )
        return AdmissionExecutionOutcome(
            outcome="completed",
            reason="task_terminalized",
            terminal_status="cancelled",
        )
