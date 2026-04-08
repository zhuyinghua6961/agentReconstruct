from __future__ import annotations

from typing import Any, Callable
import threading
import time
import uuid

from server.errors import codes
from server.errors.core import APIError
from server.schemas.request_models import PatentAskRequest
from server.services.execution_cache import ExecutionCache
from server.services.execution_lock import ExecutionLockManager, LockHandle


PreparedTurn = dict[str, Any]



def _generate_trace_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"



def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None



def _normalize_text(value: Any) -> str:
    return str(value or "").strip()



def _normalize_request_history(chat_history: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in chat_history or []:
        if not isinstance(item, dict):
            continue
        role = _normalize_text(item.get("role")).lower()
        content = str(item.get("content") or "")
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append(
            {
                "role": role,
                "content": content,
                "trace_id": _normalize_text(item.get("trace_id")),
                "created_at": _normalize_text(item.get("created_at")),
                "message_id": _normalize_text(item.get("message_id")),
            }
        )
    return normalized



def _normalize_snapshot_history(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    recent_turns = snapshot.get("recent_turns")
    if not isinstance(recent_turns, list):
        return []
    return _normalize_request_history(recent_turns)



def _normalize_authority_references(execution_result: dict[str, Any]) -> list[dict[str, Any]]:
    reference_objects = execution_result.get("reference_objects")
    if isinstance(reference_objects, list):
        normalized_objects = [dict(item) for item in reference_objects if isinstance(item, dict)]
        if normalized_objects:
            return normalized_objects

    references = execution_result.get("references")
    if not isinstance(references, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in references:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        canonical_patent_id = _normalize_text(item)
        if canonical_patent_id:
            normalized.append(
                {
                    "source_type": "patent",
                    "canonical_patent_id": canonical_patent_id,
                }
            )
    return normalized


def _normalize_mode_origin(request: PatentAskRequest, execution_result: dict[str, Any] | None = None) -> dict[str, Any]:
    options = request.options if isinstance(request.options, dict) else {}
    option_mode_origin = options.get("mode_origin") if isinstance(options.get("mode_origin"), dict) else {}
    metadata = execution_result.get("metadata") if isinstance(execution_result, dict) else None
    metadata_mode_origin = metadata.get("mode_origin") if isinstance(metadata, dict) and isinstance(metadata.get("mode_origin"), dict) else {}

    requested_mode = _normalize_text(metadata_mode_origin.get("requested_mode")) or _normalize_text(option_mode_origin.get("requested_mode"))
    execution_backend = _normalize_text(metadata_mode_origin.get("execution_backend")) or _normalize_text(option_mode_origin.get("execution_backend"))

    compatibility_route_value = metadata_mode_origin.get("compatibility_route")
    if not isinstance(compatibility_route_value, bool):
        compatibility_route_value = option_mode_origin.get("compatibility_route")

    normalized: dict[str, Any] = {}
    if requested_mode:
        normalized["requested_mode"] = requested_mode
    if execution_backend:
        normalized["execution_backend"] = execution_backend
    if isinstance(compatibility_route_value, bool):
        normalized["compatibility_route"] = compatibility_route_value
    return normalized


def _snapshot_has_converged(*, snapshot: dict[str, Any], overlay: dict[str, Any]) -> bool:
    trace_id = _normalize_text(overlay.get("trace_id"))
    if not trace_id:
        return False
    state = snapshot.get("conversation_state")
    if isinstance(state, dict) and _normalize_text(state.get("last_assistant_trace_id")) == trace_id:
        return True
    for item in snapshot.get("recent_turns") or []:
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("role")).lower() != "assistant":
            continue
        if _normalize_text(item.get("trace_id")) == trace_id:
            return True
    return False



def _merge_pending_overlays(
    *,
    snapshot: dict[str, Any],
    chat_history: list[dict[str, Any]],
    overlays: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    normalized_history = [dict(item) for item in chat_history if isinstance(item, dict)]
    normalized_overlays: list[dict[str, Any]] = []
    all_converged = bool(overlays)
    for overlay in overlays or []:
        if not isinstance(overlay, dict):
            continue
        normalized_overlay = {
            "trace_id": _normalize_text(overlay.get("trace_id")),
            "route": _normalize_text(overlay.get("route")),
            "assistant_content": _normalize_text(overlay.get("assistant_content")),
        }
        if not normalized_overlay["trace_id"] or not normalized_overlay["assistant_content"]:
            all_converged = False
            continue
        if _snapshot_has_converged(snapshot=snapshot, overlay=normalized_overlay):
            continue
        all_converged = False
        normalized_overlays.append(normalized_overlay)
        duplicate = any(
            _normalize_text(item.get("role")).lower() == "assistant"
            and _normalize_text(item.get("trace_id")) == normalized_overlay["trace_id"]
            for item in normalized_history
        )
        if duplicate:
            continue
        normalized_history.append(
            {
                "role": "assistant",
                "content": normalized_overlay["assistant_content"],
                "trace_id": normalized_overlay["trace_id"],
                "created_at": "",
                "message_id": "",
            }
        )
    return normalized_history, normalized_overlays, all_converged


class ChatPersistenceService:
    def __init__(
        self,
        *,
        authority_client: Any | None,
        execution_lock_manager: ExecutionLockManager,
        execution_cache: ExecutionCache,
        durable_mode_enabled: bool,
        lock_ttl_seconds: int = 120,
        inflight_ttl_seconds: int = 120,
        turn_state_ttl_seconds: int = 1800,
        overlay_ttl_seconds: int = 300,
        trace_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.authority_client = authority_client
        self.execution_lock_manager = execution_lock_manager
        self.execution_cache = execution_cache
        self.durable_mode_enabled = bool(durable_mode_enabled)
        self.lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self.inflight_ttl_seconds = max(1, int(inflight_ttl_seconds))
        self.turn_state_ttl_seconds = max(1, int(turn_state_ttl_seconds))
        self.overlay_ttl_seconds = max(1, int(overlay_ttl_seconds))
        self._trace_id_factory = trace_id_factory or _generate_trace_id

    def load_conversation_context(
        self,
        *,
        request: PatentAskRequest,
        user_id: int | None,
        trace_id: str,
    ) -> dict[str, Any]:
        context = {
            "persistence_mode": request.persistence_mode,
            "conversation_id": request.conversation_id,
            "trace_id": trace_id,
            "chat_history": _normalize_request_history(request.chat_history),
            "summary": {},
            "conversation_state": {},
            "snapshot": None,
            "pending_overlay": None,
            "pending_overlays": [],
        }
        resolved_user_id = _safe_positive_int(user_id)
        if not request.is_durable or resolved_user_id is None or self.authority_client is None:
            return context

        snapshot = self._read_context_snapshot(
            request=request,
            user_id=resolved_user_id,
            trace_id=trace_id,
        )
        chat_history = _normalize_snapshot_history(snapshot)
        overlay_items, overlay_raw = self.execution_cache.get_overlay_assistant_state(
            user_id=resolved_user_id,
            conversation_id=int(request.conversation_id),
        )
        merged_history, pending_overlays, should_clear = _merge_pending_overlays(
            snapshot=snapshot,
            chat_history=chat_history,
            overlays=overlay_items,
        )
        if should_clear and overlay_raw:
            self.execution_cache.delete_overlay_assistant_if_unchanged(
                user_id=resolved_user_id,
                conversation_id=int(request.conversation_id),
                raw_value=overlay_raw,
            )
        context.update(
            {
                "chat_history": merged_history,
                "summary": dict(snapshot.get("summary") or {}),
                "conversation_state": dict(snapshot.get("conversation_state") or {}),
                "snapshot": snapshot,
                "pending_overlay": dict(pending_overlays[-1]) if pending_overlays else None,
                "pending_overlays": [dict(item) for item in pending_overlays],
            }
        )
        return context

    def prepare_turn(
        self,
        *,
        request: PatentAskRequest,
        user_id: int | None,
    ) -> PreparedTurn:
        trace_id = self._resolve_trace_id(request.trace_id)
        gateway_owned_persistence = bool(request.is_gateway_owned_persistence)
        if not request.is_durable:
            context = self.load_conversation_context(request=request, user_id=user_id, trace_id=trace_id)
            return {
                "trace_id": trace_id,
                "context": context,
                "assistant_accept": None,
                "assistant_accept_required": False,
                "assistant_accept_skipped": False,
                "gateway_owned_persistence": False,
            }

        resolved_user_id = _safe_positive_int(user_id)
        self._ensure_durable_prerequisites(user_id=resolved_user_id)
        conversation_id = int(request.conversation_id)
        pending_state = self.execution_cache.get_pending_turn_state(conversation_id=conversation_id)
        pending_trace = str(pending_state.get("trace_id") or "")
        pending_user_written = bool(pending_state.get("user_written"))
        if pending_trace and pending_trace != trace_id:
            raise self._busy_error()

        cached_result = self.execution_cache.get_turn_result(
            conversation_id=conversation_id,
            trace_id=trace_id,
        )
        if isinstance(cached_result, dict):
            return self._build_cached_prepared_turn(
                request=request,
                user_id=int(resolved_user_id),
                trace_id=trace_id,
                cached_result=cached_result,
            )

        lock_handle = self._acquire_lock(conversation_id=conversation_id)
        inflight_claimed = False
        pending_claimed = False
        user_turn_written = False
        try:
            turn_identity_claimed = self.execution_cache.claim_turn_identity(
                conversation_id=conversation_id,
                trace_id=trace_id,
                ttl_seconds=self.turn_state_ttl_seconds,
            )
            if not turn_identity_claimed:
                cached_result = self.execution_cache.get_turn_result(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
                if isinstance(cached_result, dict):
                    self._cleanup_runtime_state(
                        {
                            "conversation_id": conversation_id,
                            "trace_id": trace_id,
                            "lock_handle": lock_handle,
                            "inflight_claimed": False,
                            "pending_claimed": False,
                            "user_turn_written": False,
                        }
                    )
                    return self._build_cached_prepared_turn(
                        request=request,
                        user_id=int(resolved_user_id),
                        trace_id=trace_id,
                        cached_result=cached_result,
                    )
                raise APIError(
                    code=codes.SERVICE_NOT_READY,
                    message="durable patent turn terminal state unavailable",
                    status_code=503,
                    error="service_not_ready",
                    retriable=True,
                )

            inflight_claimed = self.execution_cache.mark_turn_inflight(
                conversation_id=conversation_id,
                trace_id=trace_id,
                ttl_seconds=self.inflight_ttl_seconds,
            )
            if not inflight_claimed:
                cached_result = self.execution_cache.get_turn_result(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
                if isinstance(cached_result, dict):
                    self._cleanup_runtime_state(
                        {
                            "conversation_id": conversation_id,
                            "trace_id": trace_id,
                            "lock_handle": lock_handle,
                            "inflight_claimed": False,
                            "pending_claimed": False,
                            "user_turn_written": False,
                        }
                    )
                    return self._build_cached_prepared_turn(
                        request=request,
                        user_id=int(resolved_user_id),
                        trace_id=trace_id,
                        cached_result=cached_result,
                    )
                raise self._busy_error()

            pending_state = self.execution_cache.get_pending_turn_state(conversation_id=conversation_id)
            pending_trace = str(pending_state.get("trace_id") or "")
            pending_user_written = bool(pending_state.get("user_written"))
            if pending_trace and pending_trace != trace_id:
                raise self._busy_error()
            if not pending_trace:
                pending_claimed = self.execution_cache.claim_pending_turn(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    ttl_seconds=self.turn_state_ttl_seconds,
                    user_written=False,
                )
                if not pending_claimed:
                    pending_state = self.execution_cache.get_pending_turn_state(conversation_id=conversation_id)
                    pending_trace = str(pending_state.get("trace_id") or "")
                    pending_user_written = bool(pending_state.get("user_written"))
                    if pending_trace and pending_trace != trace_id:
                        raise self._busy_error()
                else:
                    pending_trace = trace_id
                    pending_user_written = False

            if pending_trace == trace_id and not pending_claimed:
                user_turn_written = pending_user_written
            runtime_state = {
                "conversation_id": conversation_id,
                "trace_id": trace_id,
                "user_id": int(resolved_user_id),
                "lock_handle": lock_handle,
                "inflight_claimed": inflight_claimed,
                "pending_claimed": pending_claimed,
                "user_turn_written": user_turn_written,
                "pending_clear_on_abort": gateway_owned_persistence,
                "assistant_accept_committed": False,
                "released": False,
            }
            self._start_runtime_guard_renewal(runtime_state)
            if gateway_owned_persistence:
                if pending_claimed or not pending_user_written:
                    if not self.execution_cache.mark_pending_turn_user_written(
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                        ttl_seconds=self.turn_state_ttl_seconds,
                    ):
                        raise APIError(
                            code=codes.SERVICE_NOT_READY,
                            message="durable patent pending turn marker could not be advanced for gateway-owned execution",
                            status_code=503,
                            error="service_not_ready",
                            retriable=True,
                        )
                user_turn_written = True
                runtime_state["user_turn_written"] = True
            elif pending_claimed or not user_turn_written:
                self._write_user_turn(
                    request=request,
                    user_id=int(resolved_user_id),
                    trace_id=trace_id,
                )
                user_turn_written = True
                runtime_state["user_turn_written"] = True
                if not self.execution_cache.mark_pending_turn_user_written(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    ttl_seconds=self.turn_state_ttl_seconds,
                ):
                    raise APIError(
                        code=codes.SERVICE_NOT_READY,
                        message="durable patent pending turn marker could not be advanced after user write",
                        status_code=503,
                        error="service_not_ready",
                        retriable=True,
                    )

            context = self.load_conversation_context(request=request, user_id=resolved_user_id, trace_id=trace_id)
            self._assert_runtime_state_healthy(runtime_state)
            return {
                "trace_id": trace_id,
                "context": context,
                "assistant_accept": None,
                "assistant_accept_required": not gateway_owned_persistence,
                "assistant_accept_skipped": False,
                "gateway_owned_persistence": gateway_owned_persistence,
                "_state": runtime_state,
            }
        except Exception:
            self._cleanup_runtime_state(
                {
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "lock_handle": lock_handle,
                    "inflight_claimed": inflight_claimed,
                    "pending_claimed": pending_claimed,
                    "user_turn_written": user_turn_written,
                    "pending_clear_on_abort": gateway_owned_persistence,
                }
            )
            raise

    def finalize_turn(
        self,
        prepared_turn: PreparedTurn,
        *,
        request: PatentAskRequest,
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = dict(prepared_turn or {})
        trace_id = str(prepared.get("trace_id") or self._resolve_trace_id(request.trace_id))
        context = dict(prepared.get("context") or {})
        normalized_execution_result = dict(execution_result or {})
        assistant_accept_required = bool(prepared.get("assistant_accept_required", request.is_durable))
        gateway_owned_persistence = bool(prepared.get("gateway_owned_persistence", request.is_gateway_owned_persistence))

        if prepared.get("assistant_accept_skipped"):
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": dict(prepared.get("execution_result") or normalized_execution_result),
                "assistant_accept": prepared.get("assistant_accept"),
                "assistant_accept_required": assistant_accept_required,
                "assistant_accept_skipped": True,
                "gateway_owned_persistence": gateway_owned_persistence,
            }

        if not assistant_accept_required and not gateway_owned_persistence:
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": normalized_execution_result,
                "assistant_accept": None,
                "assistant_accept_required": False,
                "assistant_accept_skipped": False,
                "gateway_owned_persistence": gateway_owned_persistence,
            }

        runtime_state = prepared.get("_state") if isinstance(prepared.get("_state"), dict) else {}
        if gateway_owned_persistence:
            try:
                self._assert_runtime_state_healthy(runtime_state)
                answer_text = _normalize_text(normalized_execution_result.get("answer_text") or normalized_execution_result.get("final_answer"))
                conversation_id = int(runtime_state.get("conversation_id") or request.conversation_id or 0)
                user_id = int(runtime_state.get("user_id") or 0)
                if not self.execution_cache.set_turn_result(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    payload={
                        "execution_result": normalized_execution_result,
                    },
                    ttl_seconds=self.turn_state_ttl_seconds,
                ):
                    raise APIError(
                        code=codes.SERVICE_NOT_READY,
                        message="durable patent turn result commit failed",
                        status_code=503,
                        error="service_not_ready",
                        retriable=True,
                    )
                if answer_text and not self.execution_cache.set_overlay_assistant(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    payload={
                        "trace_id": trace_id,
                        "route": request.route,
                        "assistant_content": answer_text,
                    },
                    ttl_seconds=self.overlay_ttl_seconds,
                ):
                    raise APIError(
                        code=codes.SERVICE_NOT_READY,
                        message="durable patent assistant overlay commit failed",
                        status_code=503,
                        error="service_not_ready",
                        retriable=True,
                    )
                if not self.execution_cache.clear_pending_turn(
                    conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
                    trace_id=trace_id,
                ):
                    raise APIError(
                        code=codes.SERVICE_NOT_READY,
                        message="durable patent pending turn clear failed",
                        status_code=503,
                        error="service_not_ready",
                        retriable=True,
                    )
                self._assert_runtime_state_healthy(runtime_state)
                return {
                    "trace_id": trace_id,
                    "context": context,
                    "execution_result": normalized_execution_result,
                    "assistant_accept": None,
                    "assistant_accept_required": False,
                    "assistant_accept_skipped": True,
                    "gateway_owned_persistence": True,
                }
            finally:
                self._cleanup_runtime_state(runtime_state)

        try:
            self._assert_runtime_state_healthy(runtime_state)
            answer_text = _normalize_text(normalized_execution_result.get("answer_text") or normalized_execution_result.get("final_answer"))
            assistant_accept = self._accept_assistant_turn(
                request=request,
                user_id=int(runtime_state.get("user_id") or 0),
                trace_id=trace_id,
                answer_text=answer_text,
                execution_result=normalized_execution_result,
            )
            if not isinstance(assistant_accept, dict) or not bool(assistant_accept.get("accepted")):
                raise APIError(
                    code=codes.AUTHORITY_UNAVAILABLE,
                    message="assistant accept not confirmed",
                    status_code=503,
                    error="authority_unavailable",
                    retriable=True,
                )
            runtime_state["assistant_accept_committed"] = True
            self._assert_runtime_state_healthy(runtime_state)
            conversation_id = int(runtime_state.get("conversation_id") or request.conversation_id or 0)
            user_id = int(runtime_state.get("user_id") or 0)
            if not self.execution_cache.set_turn_result(
                conversation_id=conversation_id,
                trace_id=trace_id,
                payload={
                    "execution_result": normalized_execution_result,
                },
                ttl_seconds=self.turn_state_ttl_seconds,
            ):
                raise APIError(
                    code=codes.SERVICE_NOT_READY,
                    message="durable patent turn result commit failed",
                    status_code=503,
                    error="service_not_ready",
                    retriable=True,
                )
            if answer_text and not self.execution_cache.set_overlay_assistant(
                user_id=user_id,
                conversation_id=conversation_id,
                payload={
                    "trace_id": trace_id,
                    "route": request.route,
                    "assistant_content": answer_text,
                },
                ttl_seconds=self.overlay_ttl_seconds,
            ):
                raise APIError(
                    code=codes.SERVICE_NOT_READY,
                    message="durable patent assistant overlay commit failed",
                    status_code=503,
                    error="service_not_ready",
                    retriable=True,
                )
            if not self.execution_cache.clear_pending_turn(
                conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
                trace_id=trace_id,
            ):
                raise APIError(
                    code=codes.SERVICE_NOT_READY,
                    message="durable patent pending turn clear failed",
                    status_code=503,
                    error="service_not_ready",
                    retriable=True,
                )
            self._assert_runtime_state_healthy(runtime_state)
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": normalized_execution_result,
                "assistant_accept": assistant_accept,
                "assistant_accept_required": True,
                "assistant_accept_skipped": False,
                "gateway_owned_persistence": gateway_owned_persistence,
            }
        finally:
            self._cleanup_runtime_state(runtime_state)

    def abort_turn(self, prepared_turn: PreparedTurn) -> None:
        prepared = dict(prepared_turn or {})
        runtime_state = prepared.get("_state") if isinstance(prepared.get("_state"), dict) else {}
        self._cleanup_runtime_state(runtime_state)

    def accept_assistant_terminal_turn(
        self,
        prepared_turn: PreparedTurn,
        *,
        request: PatentAskRequest,
        terminal_status: str,
        failure: dict[str, Any],
        answer_text: str = "",
        metadata: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
        reference_objects: list[dict[str, Any]] | None = None,
        reference_links: list[dict[str, Any]] | None = None,
        original_links: list[dict[str, Any]] | None = None,
        used_files: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        prepared = dict(prepared_turn or {})
        if prepared.get("assistant_accept_skipped"):
            return None
        if not bool(prepared.get("assistant_accept_required", request.is_durable)):
            return None

        runtime_state = prepared.get("_state") if isinstance(prepared.get("_state"), dict) else {}
        if not runtime_state:
            return None

        self._assert_runtime_state_healthy(runtime_state)
        trace_id = str(prepared.get("trace_id") or self._resolve_trace_id(request.trace_id))
        assistant_terminal_accept = self._accept_assistant_terminal_turn(
            request=request,
            user_id=int(runtime_state.get("user_id") or 0),
            trace_id=trace_id,
            terminal_status=terminal_status,
            answer_text=answer_text,
            metadata=dict(metadata or {}),
            steps=list(steps or []),
            references=list(references or []),
            reference_objects=[dict(item) for item in list(reference_objects or []) if isinstance(item, dict)],
            reference_links=[dict(item) for item in list(reference_links or []) if isinstance(item, dict)],
            original_links=[dict(item) for item in list(original_links or []) if isinstance(item, dict)],
            used_files=[dict(item) for item in list(used_files or []) if isinstance(item, dict)],
            timings=dict(timings or {}),
            failure=dict(failure or {}),
        )
        if not isinstance(assistant_terminal_accept, dict) or not bool(assistant_terminal_accept.get("accepted")):
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="assistant terminal accept not confirmed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )
        if not self.execution_cache.clear_pending_turn(
            conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
            trace_id=trace_id,
        ):
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message="durable patent pending turn clear failed after terminal accept",
                status_code=503,
                error="service_not_ready",
                retriable=True,
            )
        return assistant_terminal_accept

    def run_turn(
        self,
        *,
        request: PatentAskRequest,
        user_id: int | None,
        execute_turn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        prepared = self.prepare_turn(request=request, user_id=user_id)
        try:
            if prepared.get("assistant_accept_skipped") and isinstance(prepared.get("execution_result"), dict):
                return {
                    "trace_id": prepared["trace_id"],
                    "context": prepared["context"],
                    "execution_result": dict(prepared.get("execution_result") or {}),
                    "assistant_accept": prepared.get("assistant_accept"),
                    "assistant_accept_required": bool(prepared.get("assistant_accept_required")),
                    "assistant_accept_skipped": True,
                }
            execution_result = dict(execute_turn(dict(prepared.get("context") or {})) or {})
            return self.finalize_turn(
                prepared,
                request=request,
                execution_result=execution_result,
            )
        except Exception:
            self.abort_turn(prepared)
            raise

    def _resolve_trace_id(self, trace_id: str) -> str:
        resolved = _normalize_text(trace_id)
        return resolved or _normalize_text(self._trace_id_factory())

    def _ensure_durable_prerequisites(self, *, user_id: int | None) -> None:
        if not self.durable_mode_enabled:
            raise APIError(
                code=codes.DURABLE_MODE_DISABLED,
                message="durable patent mode is disabled",
                status_code=503,
                error="durable_mode_disabled",
                retriable=False,
            )
        if (
            user_id is None
            or self.authority_client is None
            or not self.execution_cache.available
            or not self.execution_cache.coordination_ready()
            or not self.execution_lock_manager.available
        ):
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message=f"durable patent prerequisites are not ready: {self.execution_cache.last_error or 'unknown coordination error'}",
                status_code=503,
                error="service_not_ready",
                retriable=True,
            )

    def _build_cached_prepared_turn(
        self,
        *,
        request: PatentAskRequest,
        user_id: int,
        trace_id: str,
        cached_result: dict[str, Any],
    ) -> PreparedTurn:
        execution_result = cached_result.get("execution_result") if isinstance(cached_result, dict) else None
        gateway_owned_persistence = bool(request.is_gateway_owned_persistence)
        context = self.load_conversation_context(request=request, user_id=user_id, trace_id=trace_id)
        if request.is_durable and request.conversation_id is not None:
            pending_state = self.execution_cache.get_pending_turn_state(
                conversation_id=int(request.conversation_id),
            )
            if str(pending_state.get("trace_id") or "") == trace_id:
                if self._cached_replay_visibility_ready(
                    request=request,
                    trace_id=trace_id,
                    context=context,
                    execution_result=dict(execution_result or {}),
                ):
                    if not self.execution_cache.clear_pending_turn(
                        conversation_id=int(request.conversation_id),
                        trace_id=trace_id,
                    ):
                        raise APIError(
                            code=codes.SERVICE_NOT_READY,
                            message="durable patent cached replay pending clear failed",
                            status_code=503,
                            error="service_not_ready",
                            retriable=True,
                        )
                else:
                    raise APIError(
                        code=codes.SERVICE_NOT_READY,
                        message="durable patent cached replay awaiting assistant visibility",
                        status_code=503,
                        error="service_not_ready",
                        retriable=True,
                    )
        return {
            "trace_id": trace_id,
            "context": context,
            "execution_result": dict(execution_result or {}),
            "assistant_accept": None,
            "assistant_accept_required": not gateway_owned_persistence,
            "assistant_accept_skipped": True,
            "gateway_owned_persistence": gateway_owned_persistence,
        }

    def _cleanup_runtime_state(self, runtime_state: dict[str, Any] | None) -> None:
        state = runtime_state if isinstance(runtime_state, dict) else {}
        if not state or state.get("released"):
            return
        stop_event = state.get("renew_stop")
        if isinstance(stop_event, threading.Event):
            stop_event.set()
        renew_thread = state.get("renew_thread")
        if isinstance(renew_thread, threading.Thread) and renew_thread.is_alive() and renew_thread is not threading.current_thread():
            renew_thread.join(timeout=0.05)
        conversation_id = _safe_positive_int(state.get("conversation_id"))
        trace_id = _normalize_text(state.get("trace_id"))
        if bool(state.get("inflight_claimed")) and conversation_id is not None and trace_id:
            self.execution_cache.clear_turn_inflight(
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        if (
            not bool(state.get("assistant_accept_committed"))
            and conversation_id is not None
            and trace_id
        ):
            self.execution_cache.clear_turn_identity(
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        if (
            bool(state.get("pending_claimed"))
            and (
                not bool(state.get("user_turn_written"))
                or bool(state.get("pending_clear_on_abort"))
            )
            and conversation_id is not None
            and trace_id
        ):
            self.execution_cache.clear_pending_turn(
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        lock_handle = state.get("lock_handle")
        if isinstance(lock_handle, LockHandle):
            self.execution_lock_manager.release(lock_handle.key, lock_handle.token)
        state["released"] = True

    def _cached_replay_visibility_ready(
        self,
        *,
        request: PatentAskRequest,
        trace_id: str,
        context: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> bool:
        answer_text = _normalize_text(execution_result.get("answer_text") or execution_result.get("final_answer"))
        if not answer_text:
            return True
        pending_overlays = context.get("pending_overlays") if isinstance(context.get("pending_overlays"), list) else []
        if any(str(item.get("trace_id") or "").strip() == trace_id for item in pending_overlays if isinstance(item, dict)):
            return True
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        return _snapshot_has_converged(
            snapshot=snapshot,
            overlay={
                "trace_id": trace_id,
                "route": request.route,
                "assistant_content": answer_text,
            },
        )

    def _start_runtime_guard_renewal(self, runtime_state: dict[str, Any]) -> None:
        conversation_id = _safe_positive_int(runtime_state.get("conversation_id"))
        trace_id = _normalize_text(runtime_state.get("trace_id"))
        lock_handle = runtime_state.get("lock_handle")
        if conversation_id is None or not trace_id or not isinstance(lock_handle, LockHandle):
            return
        stop_event = threading.Event()
        interval_seconds = max(0.25, min(float(self.lock_ttl_seconds), float(self.inflight_ttl_seconds)) / 3.0)

        def renew_loop() -> None:
            while not stop_event.wait(interval_seconds):
                lock_ok = self.execution_lock_manager.renew(
                    lock_handle.key,
                    lock_handle.token,
                    ttl_seconds=self.lock_ttl_seconds,
                )
                inflight_ok = self.execution_cache.renew_turn_inflight(
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    ttl_seconds=self.inflight_ttl_seconds,
                )
                if lock_ok and inflight_ok:
                    continue
                runtime_state["renew_error"] = self.execution_lock_manager.last_error or self.execution_cache.last_error or "runtime guard renew failed"
                stop_event.set()
                return

        renew_thread = threading.Thread(
            target=renew_loop,
            name=f"patent-turn-renew-{trace_id}",
            daemon=True,
        )
        runtime_state["renew_stop"] = stop_event
        runtime_state["renew_thread"] = renew_thread
        renew_thread.start()

    def _assert_runtime_state_healthy(self, runtime_state: dict[str, Any]) -> None:
        error_message = _normalize_text(runtime_state.get("renew_error"))
        if not error_message:
            return
        raise APIError(
            code=codes.SERVICE_NOT_READY,
            message=f"durable patent runtime guard renewal failed: {error_message}",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )

    def _acquire_lock(self, *, conversation_id: int) -> LockHandle:
        try:
            handle = self.execution_lock_manager.acquire_conversation_lock(
                conversation_id=conversation_id,
                ttl_seconds=self.lock_ttl_seconds,
            )
        except Exception as exc:
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message=f"durable patent lock unavailable: {exc}",
                status_code=503,
                error="service_not_ready",
                retriable=True,
            ) from exc
        if handle is None:
            raise self._busy_error()
        return handle

    def _write_user_turn(self, *, request: PatentAskRequest, user_id: int, trace_id: str) -> dict[str, Any]:
        mode_origin = _normalize_mode_origin(request)
        try:
            return self.authority_client.write_user_turn(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                source_scope=request.source_scope,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
                content=request.question,
                selected_file_ids=list(request.selected_file_ids),
                last_turn_route_hint=request.route,
                mode_origin_requested_mode=_normalize_text(mode_origin.get("requested_mode")),
                mode_origin_execution_backend=_normalize_text(mode_origin.get("execution_backend")),
                compatibility_route=mode_origin.get("compatibility_route") if isinstance(mode_origin.get("compatibility_route"), bool) else None,
            )
        except Exception as exc:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message=f"user write failed: {exc}",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            ) from exc

    def _read_context_snapshot(self, *, request: PatentAskRequest, user_id: int, trace_id: str) -> dict[str, Any]:
        try:
            return self.authority_client.read_context_snapshot(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                source_scope=request.source_scope,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
            )
        except Exception as exc:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message=f"context snapshot failed: {exc}",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            ) from exc

    def _accept_assistant_turn(
        self,
        *,
        request: PatentAskRequest,
        user_id: int,
        trace_id: str,
        answer_text: str,
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = dict(execution_result.get("metadata") or {})
        mode_origin = _normalize_mode_origin(request, execution_result)
        if mode_origin:
            existing_mode_origin = metadata.get("mode_origin")
            merged_mode_origin = dict(existing_mode_origin) if isinstance(existing_mode_origin, dict) else {}
            for key, value in mode_origin.items():
                merged_mode_origin[key] = value
            metadata["mode_origin"] = merged_mode_origin
        try:
            return self.authority_client.accept_assistant_turn_async(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                source_scope=request.source_scope,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
                answer_text=answer_text,
                metadata=metadata,
                steps=list(execution_result.get("steps") or []),
                references=_normalize_authority_references(execution_result),
                reference_objects=[dict(item) for item in list(execution_result.get("reference_objects") or []) if isinstance(item, dict)],
                reference_links=[dict(item) for item in list(execution_result.get("reference_links") or []) if isinstance(item, dict)],
                original_links=[dict(item) for item in list(execution_result.get("original_links") or []) if isinstance(item, dict)],
                used_files=list(execution_result.get("used_files") or []),
                timings=dict(execution_result.get("timings") or {}),
            )
        except Exception as exc:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message=f"assistant accept failed: {exc}",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            ) from exc

    def _accept_assistant_terminal_turn(
        self,
        *,
        request: PatentAskRequest,
        user_id: int,
        trace_id: str,
        terminal_status: str,
        answer_text: str,
        metadata: dict[str, Any],
        steps: list[dict[str, Any]],
        references: list[dict[str, Any]],
        reference_objects: list[dict[str, Any]],
        reference_links: list[dict[str, Any]],
        original_links: list[dict[str, Any]],
        used_files: list[dict[str, Any]],
        timings: dict[str, Any],
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        mode_origin = _normalize_mode_origin(request, {"metadata": metadata})
        if mode_origin:
            existing_mode_origin = metadata.get("mode_origin")
            merged_mode_origin = dict(existing_mode_origin) if isinstance(existing_mode_origin, dict) else {}
            for key, value in mode_origin.items():
                merged_mode_origin[key] = value
            metadata["mode_origin"] = merged_mode_origin
        try:
            return self.authority_client.accept_assistant_terminal_async(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                source_scope=request.source_scope,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
                terminal_status=terminal_status,
                answer_text=answer_text,
                metadata=metadata,
                steps=steps,
                references=references,
                reference_objects=reference_objects,
                reference_links=reference_links,
                original_links=original_links,
                used_files=used_files,
                timings=timings,
                failure=failure,
            )
        except Exception as exc:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message=f"assistant terminal accept failed: {exc}",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            ) from exc

    @staticmethod
    def _busy_error() -> APIError:
        return APIError(
            code=codes.PATENT_BUSY,
            message="durable patent turn is already in flight",
            status_code=409,
            error="patent_busy",
            retriable=True,
        )
