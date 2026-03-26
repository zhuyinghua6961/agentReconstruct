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



def _merge_pending_overlay(
    *,
    snapshot: dict[str, Any],
    chat_history: list[dict[str, Any]],
    overlay: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    normalized_history = [dict(item) for item in chat_history if isinstance(item, dict)]
    if not isinstance(overlay, dict):
        return normalized_history, None, False
    normalized_overlay = {
        "trace_id": _normalize_text(overlay.get("trace_id")),
        "route": _normalize_text(overlay.get("route")),
        "assistant_content": _normalize_text(overlay.get("assistant_content")),
    }
    if not normalized_overlay["trace_id"] or not normalized_overlay["assistant_content"]:
        return normalized_history, None, False
    if _snapshot_has_converged(snapshot=snapshot, overlay=normalized_overlay):
        return normalized_history, None, True
    for item in normalized_history:
        if _normalize_text(item.get("role")).lower() != "assistant":
            continue
        if _normalize_text(item.get("trace_id")) == normalized_overlay["trace_id"]:
            return normalized_history, normalized_overlay, False
    normalized_history.append(
        {
            "role": "assistant",
            "content": normalized_overlay["assistant_content"],
            "trace_id": normalized_overlay["trace_id"],
            "created_at": "",
            "message_id": "",
        }
    )
    return normalized_history, normalized_overlay, False


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
        overlay = self.execution_cache.get_overlay_assistant(
            user_id=resolved_user_id,
            conversation_id=int(request.conversation_id),
        )
        merged_history, pending_overlay, should_clear = _merge_pending_overlay(
            snapshot=snapshot,
            chat_history=chat_history,
            overlay=overlay,
        )
        if should_clear:
            self.execution_cache.delete_overlay_assistant(
                user_id=resolved_user_id,
                conversation_id=int(request.conversation_id),
            )
        context.update(
            {
                "chat_history": merged_history,
                "summary": dict(snapshot.get("summary") or {}),
                "conversation_state": dict(snapshot.get("conversation_state") or {}),
                "snapshot": snapshot,
                "pending_overlay": pending_overlay,
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
        if not request.is_durable:
            context = self.load_conversation_context(request=request, user_id=user_id, trace_id=trace_id)
            return {
                "trace_id": trace_id,
                "context": context,
                "assistant_accept": None,
                "assistant_accept_required": False,
                "assistant_accept_skipped": False,
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
                "released": False,
            }
            self._start_runtime_guard_renewal(runtime_state)
            if pending_claimed or not user_turn_written:
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
                "assistant_accept_required": True,
                "assistant_accept_skipped": False,
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

        if prepared.get("assistant_accept_skipped"):
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": dict(prepared.get("execution_result") or normalized_execution_result),
                "assistant_accept": prepared.get("assistant_accept"),
                "assistant_accept_required": assistant_accept_required,
                "assistant_accept_skipped": True,
            }

        if not assistant_accept_required:
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": normalized_execution_result,
                "assistant_accept": None,
                "assistant_accept_required": False,
                "assistant_accept_skipped": False,
            }

        runtime_state = prepared.get("_state") if isinstance(prepared.get("_state"), dict) else {}
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
            self._assert_runtime_state_healthy(runtime_state)
            if answer_text:
                self.execution_cache.set_overlay_assistant(
                    user_id=int(runtime_state.get("user_id") or 0),
                    conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
                    payload={
                        "trace_id": trace_id,
                        "route": request.route,
                        "assistant_content": answer_text,
                    },
                    ttl_seconds=self.overlay_ttl_seconds,
                )
            self.execution_cache.set_turn_result(
                conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
                trace_id=trace_id,
                payload={
                    "execution_result": normalized_execution_result,
                },
                ttl_seconds=self.turn_state_ttl_seconds,
            )
            self.execution_cache.clear_pending_turn(
                conversation_id=int(runtime_state.get("conversation_id") or request.conversation_id or 0),
                trace_id=trace_id,
            )
            self._assert_runtime_state_healthy(runtime_state)
            return {
                "trace_id": trace_id,
                "context": context,
                "execution_result": normalized_execution_result,
                "assistant_accept": assistant_accept,
                "assistant_accept_required": True,
                "assistant_accept_skipped": False,
            }
        finally:
            self._cleanup_runtime_state(runtime_state)

    def abort_turn(self, prepared_turn: PreparedTurn) -> None:
        prepared = dict(prepared_turn or {})
        runtime_state = prepared.get("_state") if isinstance(prepared.get("_state"), dict) else {}
        self._cleanup_runtime_state(runtime_state)

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
            or not self.execution_lock_manager.available
        ):
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message="durable patent prerequisites are not ready",
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
        self.execution_cache.clear_pending_turn(
            conversation_id=int(request.conversation_id),
            trace_id=trace_id,
        )
        context = self.load_conversation_context(request=request, user_id=user_id, trace_id=trace_id)
        execution_result = cached_result.get("execution_result") if isinstance(cached_result, dict) else None
        return {
            "trace_id": trace_id,
            "context": context,
            "execution_result": dict(execution_result or {}),
            "assistant_accept": None,
            "assistant_accept_required": True,
            "assistant_accept_skipped": True,
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
            bool(state.get("pending_claimed"))
            and not bool(state.get("user_turn_written"))
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
        try:
            return self.authority_client.write_user_turn(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
                content=request.question,
                selected_file_ids=list(request.selected_file_ids),
                last_turn_route_hint=request.route,
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
        try:
            return self.authority_client.accept_assistant_turn_async(
                user_id=user_id,
                conversation_id=int(request.conversation_id),
                trace_id=trace_id,
                route=request.route,
                requested_mode=request.requested_mode,
                actual_mode=request.actual_mode,
                answer_text=answer_text,
                steps=list(execution_result.get("steps") or []),
                references=list(execution_result.get("references") or []),
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

    @staticmethod
    def _busy_error() -> APIError:
        return APIError(
            code=codes.PATENT_BUSY,
            message="durable patent turn is already in flight",
            status_code=409,
            error="patent_busy",
            retriable=True,
        )
