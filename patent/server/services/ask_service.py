from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterator

from server.errors import codes
from server.errors.core import APIError
from server.patent.executor import PatentExecutor
from server.patent.stream_events import (
    final_content_source_for_route,
    structured_content_streaming_enabled,
)
from server.patent.result_builder import PatentResultBuilder, default_now_factory
from server.runtime.request_context import clear_trace_id, set_trace_id
from server.schemas.request_models import PatentAskRequest
from server.services.conversation_context_builder import (
    build_patent_conversation_context,
    normalize_patent_conversation_context,
)
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


def _epoch_ms() -> int:
    return max(0, int(time.time() * 1000))


_CANCELLED_WORKER_JOIN_TIMEOUT_SECONDS = 0.2
_CANCEL_QUEUE_POLL_SECONDS = 0.05


class _PatentStreamCancelled(Exception):
    pass


def _attach_event_telemetry(event: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event or {})
    metadata = dict(payload.get("metadata") or {})
    metadata["telemetry"] = {
        key: int(value)
        for key, value in dict(telemetry or {}).items()
        if isinstance(value, int) and value >= 0
    }
    payload["metadata"] = metadata
    return payload


def _build_context_ready_steps(*, request: PatentAskRequest, raw_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    context_payload = dict(raw_context or {})
    if any(key in context_payload for key in ("recent_turns_for_llm", "summary_for_llm", "source_selection")):
        normalized_context = normalize_patent_conversation_context(
            recent_turns_for_llm=context_payload.get("recent_turns_for_llm"),
            summary_for_llm=context_payload.get("summary_for_llm"),
            conversation_state=context_payload.get("conversation_state"),
            source_selection=context_payload.get("source_selection"),
        )
    else:
        normalized_context = build_patent_conversation_context(
            request=request,
            raw_context=context_payload,
        )
    context_turns = len(list(normalized_context.get("recent_turns_for_llm") or []))
    summary_available = bool(normalized_context.get("summary_for_llm"))
    message = f"已完成上下文整理（最近 {context_turns} 条消息）"
    if summary_available:
        message += "并加载会话摘要"
    return [
        {
            "step": "context_ready",
            "message": message,
            "status": "success",
            "data": {
                "count": context_turns,
                "context_turns": context_turns,
                "summary_available": summary_available,
            },
        }
    ]


def _build_gateway_prestream_step() -> dict[str, Any]:
    return {
        "step": "stage1",
        "title": "阶段一",
        "message": "阶段一：已开始上下文准备与检索规划",
        "status": "processing",
        "data": {"preparing_context": True},
    }


def _prepend_steps(*, steps: list[dict[str, Any]], leading_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(leading_steps or []) + list(steps or []):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        step_key = str(payload.get("step") or "").strip()
        if step_key and step_key in seen:
            continue
        if step_key:
            seen.add(step_key)
        merged.append(payload)
    return merged


def _attach_preflight_steps(
    *,
    execution_result: dict[str, Any],
    preflight_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(execution_result or {})
    merged_steps = _prepend_steps(
        steps=list(payload.get("steps") or []),
        leading_steps=preflight_steps,
    )
    payload["steps"] = merged_steps
    metadata = dict(payload.get("metadata") or {})
    if merged_steps:
        metadata["steps"] = [dict(item) for item in merged_steps]
    payload["metadata"] = metadata
    return payload


class AskService:
    def __init__(
        self,
        *,
        patent_executor: PatentExecutor | None = None,
        persistence_service: Any,
        mode_profile: PatentModeProfile | None = None,
        now_factory: Callable[[], str] | None = None,
    ) -> None:
        self._logger = logging.getLogger("patent.ask_service")
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._patent_executor = patent_executor or PatentExecutor(mode_profile=self._mode_profile)
        self._persistence_service = persistence_service
        self._result_builder = PatentResultBuilder(
            mode_profile=self._mode_profile,
            now_factory=now_factory or default_now_factory,
        )

    def sync_ask(self, request: PatentAskRequest, *, user_id: int | None) -> dict[str, Any]:
        trace_token = set_trace_id(str(request.trace_id))
        resolved_trace_id = str(request.trace_id)
        prepared: dict[str, Any] = {}
        try:
            prepared = self._prepare_turn(request=request, user_id=user_id)
            resolved_trace_id = str(prepared.get("trace_id") or resolved_trace_id)
            self._logger.info("sync_ask start trace_id=%s durable=%s", resolved_trace_id, request.is_durable)
            preflight_steps = _build_context_ready_steps(
                request=request,
                raw_context=dict(prepared.get("context") or {}),
            )
            try:
                turn_result = self._complete_turn(request=request, prepared_turn=prepared)
                execution_result = _attach_preflight_steps(
                    execution_result=dict(turn_result.get("execution_result") or {}),
                    preflight_steps=preflight_steps,
                )
                self._logger.info(
                    "sync_ask complete trace_id=%s answer_chars=%s",
                    turn_result.get("trace_id") or prepared.get("trace_id") or resolved_trace_id,
                    len(str(execution_result.get("answer_text") or "")),
                )
                return self._result_builder.build_sync_success(
                    request=request,
                    trace_id=str(turn_result.get("trace_id") or prepared.get("trace_id") or resolved_trace_id),
                    execution_result=execution_result,
                )
            except Exception as exc:
                self._logger.exception("sync_ask failed trace_id=%s error=%s", resolved_trace_id, exc)
                self._persist_terminal_failure(request=request, prepared_turn=prepared, exc=exc)
                self._abort_turn(prepared)
                raise self._result_builder.to_api_error(exc) from exc
        finally:
            clear_trace_id(trace_token)

    def stream_ask(
        self,
        request: PatentAskRequest,
        *,
        user_id: int | None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        trace_token = set_trace_id(str(request.trace_id))
        prepared: dict[str, Any] = {}
        trace_id = str(request.trace_id)
        seq = 0
        telemetry: dict[str, int] = {}
        prepare_queue: queue.Queue[tuple[str, Any]] | None = None
        prepare_cancelled: threading.Event | None = None
        execution_cancelled = threading.Event()
        worker: threading.Thread | None = None
        stream_completed = False
        terminal_persisted = False
        turn_aborted = False
        structured_file_streaming = structured_content_streaming_enabled(
            options=request.options,
            route=request.route,
        )

        def _mark_telemetry_once(field_name: str) -> None:
            if field_name not in telemetry:
                telemetry[field_name] = _epoch_ms()

        def _abort_turn_once() -> None:
            nonlocal turn_aborted
            if turn_aborted or not prepared:
                return
            self._abort_turn(prepared)
            turn_aborted = True

        def _should_cancel_execution() -> bool:
            return execution_cancelled.is_set() or (cancel_event is not None and cancel_event.is_set())

        def _raise_if_cancelled() -> None:
            if _should_cancel_execution():
                if prepare_cancelled is not None:
                    prepare_cancelled.set()
                raise _PatentStreamCancelled("stream cancelled")

        try:
            if request.is_gateway_owned_persistence and request.is_durable:
                prepare_queue = queue.Queue(maxsize=1)
                prepare_cancelled = threading.Event()

                def _prepare_turn_worker() -> None:
                    worker_trace_token = set_trace_id(trace_id)
                    try:
                        prepared_turn = self._prepare_turn(request=request, user_id=user_id)
                        if prepare_cancelled is not None and prepare_cancelled.is_set():
                            self._abort_turn(dict(prepared_turn or {}))
                            return
                        prepare_queue.put(("prepared", prepared_turn))
                    except Exception as exc:
                        prepare_queue.put(("exception", exc))
                    finally:
                        clear_trace_id(worker_trace_token)

                threading.Thread(
                    target=_prepare_turn_worker,
                    name=f"patent-prepare-{trace_id or 'unknown'}",
                    daemon=True,
                ).start()
            else:
                prepared = self._prepare_turn(request=request, user_id=user_id)
                trace_id = str(prepared.get("trace_id") or trace_id)
            self._logger.info("stream_ask start trace_id=%s durable=%s", trace_id, request.is_durable)
            _mark_telemetry_once("backend_stream_opened_at_ms")
            yield _attach_event_telemetry(
                self._result_builder.build_metadata_event(
                    trace_id=trace_id,
                    seq=seq,
                    route=request.route,
                    query_mode=get_patent_mode_profile(request.route).query_mode,
                    source_scope=request.source_scope,
                ),
                telemetry,
            )
            seq += 1
            if prepare_queue is not None:
                _mark_telemetry_once("first_step_at_ms")
                yield self._result_builder.build_step_event(seq=seq, step=_build_gateway_prestream_step())
                seq += 1
                while True:
                    _raise_if_cancelled()
                    try:
                        prepare_result_type, prepare_payload = prepare_queue.get(timeout=_CANCEL_QUEUE_POLL_SECONDS)
                        break
                    except queue.Empty:
                        continue
                if prepare_result_type == "exception":
                    raise prepare_payload
                prepared = dict(prepare_payload or {})
                trace_id = str(prepared.get("trace_id") or trace_id)
                _raise_if_cancelled()
            preflight_steps = _build_context_ready_steps(
                request=request,
                raw_context=dict(prepared.get("context") or {}),
            )
            if prepared.get("assistant_accept_skipped") and isinstance(prepared.get("execution_result"), dict):
                execution_result = _attach_preflight_steps(
                    execution_result=self._validate_execution_result(
                        request=request,
                        trace_id=trace_id,
                        execution_result=dict(prepared.get("execution_result") or {}),
                    ),
                    preflight_steps=preflight_steps,
                )
                progress_events: list[dict[str, Any]] = []
                next_seq = seq
                for step in list(execution_result.get("steps") or []):
                    progress_events.append(
                        self._result_builder.build_step_event(
                            seq=next_seq,
                            step=dict(step or {}),
                        )
                    )
                    next_seq += 1
                answer_text = str(execution_result.get("answer_text") or "")
                if answer_text:
                    progress_events.append(
                        self._build_single_answer_content_event(
                            request=request,
                            seq=next_seq,
                            content=answer_text,
                        )
                    )
                for event in progress_events:
                    if str(event.get("type") or "") == "step":
                        _mark_telemetry_once("first_step_at_ms")
                    if str(event.get("type") or "") == "content":
                        _mark_telemetry_once("first_content_at_ms")
                    yield event
                seq += len(progress_events)
                self._ensure_done_allowed(prepared)
                stream_completed = True
                yield _attach_event_telemetry(
                    self._result_builder.build_done_event(
                        request=request,
                        trace_id=trace_id,
                        execution_result=execution_result,
                        seq=seq,
                    ),
                    telemetry,
                )
                return

            progress_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            for step in preflight_steps:
                _mark_telemetry_once("first_step_at_ms")
                yield self._result_builder.build_step_event(seq=seq, step=step)
                seq += 1

            streamed_step_count = len(preflight_steps)
            streamed_content_count = 0

            def _progress_callback(step: dict[str, Any]) -> None:
                progress_queue.put(("progress", dict(step or {})))

            def _content_callback(chunk: Any) -> None:
                if isinstance(chunk, dict):
                    progress_queue.put(("content", dict(chunk)))
                    return
                progress_queue.put(("content", str(chunk or "")))

            def _execute_worker() -> None:
                worker_trace_token = set_trace_id(trace_id)
                try:
                    execution_result = self._validate_execution_result(
                        request=request,
                        trace_id=trace_id,
                        execution_result=self._execute_turn(
                            request=request,
                            context=dict(prepared.get("context") or {}),
                            progress_callback=_progress_callback,
                            content_callback=_content_callback,
                            should_cancel=_should_cancel_execution,
                        ),
                    )
                except Exception as exc:  # pragma: no cover - exercised through stream error assertions
                    progress_queue.put(("exception", exc))
                    return
                finally:
                    clear_trace_id(worker_trace_token)
                progress_queue.put(("result", execution_result))

            worker = threading.Thread(
                target=_execute_worker,
                name=f"patent-stream-{trace_id or 'unknown'}",
                daemon=True,
            )
            worker.start()

            execution_result: dict[str, Any] | None = None
            while execution_result is None:
                _raise_if_cancelled()
                try:
                    event_type, payload = progress_queue.get(timeout=_CANCEL_QUEUE_POLL_SECONDS)
                except queue.Empty:
                    continue
                _raise_if_cancelled()
                if event_type == "progress":
                    _mark_telemetry_once("first_step_at_ms")
                    yield self._result_builder.build_step_event(seq=seq, step=dict(payload or {}))
                    seq += 1
                    streamed_step_count += 1
                    continue
                if event_type == "content":
                    if isinstance(payload, dict):
                        chunk = str(payload.get("content") or "")
                        phase = str(payload.get("content_phase") or "")
                        if chunk or phase == "end":
                            _mark_telemetry_once("first_content_at_ms")
                            yield self._result_builder.build_content_event(
                                seq=seq,
                                content=chunk,
                                content_role=payload.get("content_role"),
                                content_source=payload.get("content_source"),
                                content_stream_id=payload.get("content_stream_id"),
                                content_phase=payload.get("content_phase"),
                                replace_stream=payload.get("replace_stream"),
                            )
                            seq += 1
                            streamed_content_count += 1
                        continue
                    chunk = str(payload or "")
                    if chunk:
                        _mark_telemetry_once("first_content_at_ms")
                        yield self._result_builder.build_content_event(seq=seq, content=chunk)
                        seq += 1
                        streamed_content_count += 1
                    continue
                if event_type == "exception":
                    raise payload
                if event_type == "result":
                    execution_result = _attach_preflight_steps(
                        execution_result=dict(payload or {}),
                        preflight_steps=preflight_steps,
                    )
                    _raise_if_cancelled()
                    break

            if streamed_step_count == 0:
                for event in self._result_builder.iter_progress_events(
                    execution_result=execution_result,
                    starting_seq=seq,
                ):
                    event_type = str(event.get("type") or "")
                    if streamed_content_count > 0 and event_type == "content":
                        continue
                    if event_type == "step":
                        _mark_telemetry_once("first_step_at_ms")
                    if event_type == "content":
                        _mark_telemetry_once("first_content_at_ms")
                    yield event
                    seq += 1
            else:
                answer_text = str(execution_result.get("answer_text") or "")
                if answer_text and streamed_content_count == 0:
                    _raise_if_cancelled()
                    _mark_telemetry_once("first_content_at_ms")
                    yield self._build_single_answer_content_event(
                        request=request,
                        seq=seq,
                        content=answer_text,
                    )
                    seq += 1

            _raise_if_cancelled()
            turn_result = self._finalize_turn(
                request=request,
                prepared_turn=prepared,
                execution_result=execution_result,
            )
            trace_id = str(turn_result.get("trace_id") or trace_id)
            self._ensure_done_allowed(turn_result)
            _raise_if_cancelled()
            stream_completed = True
            yield _attach_event_telemetry(
                self._result_builder.build_done_event(
                    request=request,
                    trace_id=trace_id,
                    execution_result=dict(turn_result.get("execution_result") or {}),
                    seq=seq,
                ),
                telemetry,
            )
        except _PatentStreamCancelled as exc:
            terminal_persisted = True
            self._persist_terminal_cancellation(request=request, prepared_turn=prepared)
            _abort_turn_once()
            yield self._build_error_event(trace_id=trace_id, seq=seq, exc=exc)
        except Exception as exc:
            self._logger.exception("stream_ask failed trace_id=%s error=%s", trace_id, exc)
            self._persist_terminal_failure(request=request, prepared_turn=prepared, exc=exc)
            terminal_persisted = True
            _abort_turn_once()
            yield self._build_error_event(trace_id=trace_id, seq=seq, exc=exc)
        finally:
            if not stream_completed:
                execution_cancelled.set()
                if worker is not None and worker.is_alive():
                    worker.join(timeout=_CANCELLED_WORKER_JOIN_TIMEOUT_SECONDS)
                    if worker.is_alive():
                        self._logger.warning(
                            "patent stream worker still running after cancellation trace_id=%s worker=%s",
                            trace_id,
                            worker.name,
                        )
                if prepare_cancelled is not None:
                    prepare_cancelled.set()
                if prepare_queue is not None and not prepared:
                    drain_deadline = time.monotonic() + _CANCELLED_WORKER_JOIN_TIMEOUT_SECONDS
                    while time.monotonic() < drain_deadline:
                        try:
                            prepare_result_type, prepare_payload = prepare_queue.get_nowait()
                        except queue.Empty:
                            time.sleep(_CANCEL_QUEUE_POLL_SECONDS)
                            continue
                        if prepare_result_type == "prepared":
                            prepared = dict(prepare_payload or {})
                        break
                if not terminal_persisted:
                    self._persist_terminal_cancellation(request=request, prepared_turn=prepared)
                _abort_turn_once()
            clear_trace_id(trace_token)

    def _prepare_turn(self, *, request: PatentAskRequest, user_id: int | None) -> dict[str, Any]:
        prepare_turn = getattr(self._persistence_service, "prepare_turn", None)
        if callable(prepare_turn):
            return dict(prepare_turn(request=request, user_id=user_id) or {})
        fallback_result = dict(
            self._persistence_service.run_turn(
                request=request,
                user_id=user_id,
                execute_turn=lambda context: self._execute_and_validate_fallback_turn(
                    request=request,
                    context=context,
                ),
            )
            or {}
        )
        fallback_result["_completed_turn"] = True
        return fallback_result

    def _execute_and_validate_fallback_turn(
        self,
        *,
        request: PatentAskRequest,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resolved_trace_id = str((context or {}).get("trace_id") or request.trace_id).strip()
        if resolved_trace_id and resolved_trace_id != str(request.trace_id):
            effective_request = replace(request, trace_id=resolved_trace_id)
        else:
            effective_request = request
        return self._validate_execution_result(
            request=effective_request,
            trace_id=resolved_trace_id or str(request.trace_id),
            execution_result=self._execute_turn(
                request=effective_request,
                context=dict(context or {}),
            ),
        )

    def _finalize_turn(
        self,
        *,
        request: PatentAskRequest,
        prepared_turn: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        finalize_turn = getattr(self._persistence_service, "finalize_turn", None)
        if callable(finalize_turn):
            return dict(
                finalize_turn(
                    prepared_turn,
                    request=request,
                    execution_result=execution_result,
                )
                or {}
            )
        return {
            **dict(prepared_turn or {}),
            "execution_result": dict(execution_result or {}),
        }

    def _complete_turn(self, *, request: PatentAskRequest, prepared_turn: dict[str, Any]) -> dict[str, Any]:
        preflight_steps = _build_context_ready_steps(
            request=request,
            raw_context=dict(prepared_turn.get("context") or {}),
        )
        if prepared_turn.get("_completed_turn") and isinstance(prepared_turn.get("execution_result"), dict):
            execution_result = _attach_preflight_steps(
                execution_result=self._validate_execution_result(
                    request=request,
                    trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
                    execution_result=dict(prepared_turn.get("execution_result") or {}),
                ),
                preflight_steps=preflight_steps,
            )
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": execution_result,
            }
        if prepared_turn.get("assistant_accept_skipped") and isinstance(prepared_turn.get("execution_result"), dict):
            execution_result = _attach_preflight_steps(
                execution_result=self._validate_execution_result(
                    request=request,
                    trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
                    execution_result=dict(prepared_turn.get("execution_result") or {}),
                ),
                preflight_steps=preflight_steps,
            )
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": execution_result,
            }
        execution_result = _attach_preflight_steps(
            execution_result=self._validate_execution_result(
                request=request,
                trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
                execution_result=self._execute_turn(request=request, context=dict(prepared_turn.get("context") or {})),
            ),
            preflight_steps=preflight_steps,
        )
        turn_result = self._finalize_turn(
            request=request,
            prepared_turn=prepared_turn,
            execution_result=execution_result,
        )
        self._ensure_done_allowed(turn_result)
        return turn_result

    def _abort_turn(self, prepared_turn: dict[str, Any]) -> None:
        abort_turn = getattr(self._persistence_service, "abort_turn", None)
        if callable(abort_turn):
            abort_turn(prepared_turn)

    def _build_error_event(self, *, trace_id: str, seq: int, exc: Exception) -> dict[str, Any]:
        api_error = self._result_builder.to_api_error(exc)
        try:
            return self._result_builder.build_error_event(trace_id=trace_id, seq=seq, error=api_error)
        except Exception:
            return {
                "type": "error",
                "code": str(api_error.code),
                "error": str(api_error.error),
                "message": str(api_error.message),
                "trace_id": str(trace_id),
                "seq": int(seq),
                "ts": "1970-01-01T00:00:00Z",
            }

    def _build_single_answer_content_event(
        self,
        *,
        request: PatentAskRequest,
        seq: int,
        content: str,
    ) -> dict[str, Any]:
        if structured_content_streaming_enabled(options=request.options, route=request.route):
            return self._result_builder.build_content_event(
                seq=seq,
                content=content,
                content_role="final",
                content_source=final_content_source_for_route(request.route),
                content_stream_id="final:answer",
                content_phase="snapshot",
                replace_stream=True,
            )
        return self._result_builder.build_content_event(seq=seq, content=content)

    def _persist_terminal_failure(
        self,
        *,
        request: PatentAskRequest,
        prepared_turn: dict[str, Any],
        exc: Exception,
    ) -> None:
        accept_terminal_turn = getattr(self._persistence_service, "accept_assistant_terminal_turn", None)
        if not callable(accept_terminal_turn):
            return
        prepared = dict(prepared_turn or {})
        if not prepared:
            return
        api_error = self._result_builder.to_api_error(exc)
        failed_stage = self._infer_failed_stage(exc, api_error=api_error)
        try:
            accept_terminal_turn(
                prepared,
                request=request,
                terminal_status="failed",
                answer_text="",
                metadata={},
                steps=self._extract_failure_steps(exc, failed_stage=failed_stage),
                timings=self._extract_failure_timings(exc),
                failure={
                    "stage": failed_stage or None,
                    "message": str(api_error.message),
                    "code": str(api_error.code),
                    "retriable": bool(api_error.retriable),
                },
            )
        except Exception as persist_exc:
            self._logger.exception(
                "terminal failure persistence failed trace=%s error=%s",
                prepared.get("trace_id") or request.trace_id,
                persist_exc,
            )

    def _persist_terminal_cancellation(
        self,
        *,
        request: PatentAskRequest,
        prepared_turn: dict[str, Any],
    ) -> None:
        accept_terminal_turn = getattr(self._persistence_service, "accept_assistant_terminal_turn", None)
        if not callable(accept_terminal_turn):
            return
        prepared = dict(prepared_turn or {})
        if not prepared:
            return
        try:
            accept_terminal_turn(
                prepared,
                request=request,
                terminal_status="canceled",
                answer_text="",
                metadata={},
                steps=[],
                timings={},
                failure={
                    "stage": None,
                    "message": "stream canceled by client",
                    "code": "client_canceled",
                    "retriable": True,
                },
            )
        except Exception as persist_exc:
            self._logger.exception(
                "terminal cancellation persistence failed trace=%s error=%s",
                prepared.get("trace_id") or request.trace_id,
                persist_exc,
            )

    @staticmethod
    def _infer_failed_stage(exc: Exception, *, api_error: APIError) -> str:
        if isinstance(exc, APIError):
            stage_from_extra = str(exc.extra.get("failed_stage") or "").strip()
            if stage_from_extra:
                return stage_from_extra
        message = str(api_error.message or "").strip()
        match = re.search(r"\bat\s+([A-Za-z0-9_.-]+)\s*$", message)
        return str(match.group(1) if match else "").strip()

    @staticmethod
    def _extract_failure_steps(exc: Exception, *, failed_stage: str) -> list[dict[str, Any]]:
        if isinstance(exc, APIError):
            raw_steps = exc.extra.get("steps")
            if isinstance(raw_steps, list):
                return [dict(item) for item in raw_steps if isinstance(item, dict)]
        if not failed_stage:
            return []
        return [
            {
                "step": failed_stage,
                "title": failed_stage.replace("stage", "Stage ").strip().title(),
                "message": f"{failed_stage} failed.",
                "status": "failed",
            }
        ]

    @staticmethod
    def _extract_failure_timings(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, APIError) and isinstance(exc.extra.get("timings"), dict):
            return dict(exc.extra.get("timings") or {})
        return {}

    def _ensure_done_allowed(self, turn_result: dict[str, Any]) -> None:
        assistant_accept_required = bool(turn_result.get("assistant_accept_required"))
        if not assistant_accept_required:
            return
        if bool(turn_result.get("assistant_accept_skipped")):
            return
        assistant_accept = turn_result.get("assistant_accept")
        if isinstance(assistant_accept, dict) and bool(assistant_accept.get("accepted")):
            return
        raise APIError(
            code=codes.AUTHORITY_UNAVAILABLE,
            message="assistant accept not confirmed",
            status_code=503,
            error="authority_unavailable",
            retriable=True,
        )

    def _execute_turn(
        self,
        *,
        request: PatentAskRequest,
        context: dict[str, Any],
        progress_callback: Any | None = None,
        content_callback: Any | None = None,
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        execute_with_progress = getattr(self._patent_executor, "execute_with_progress", None)
        if callable(execute_with_progress):
            return dict(
                execute_with_progress(
                    request=request,
                    context=context,
                    progress_callback=progress_callback,
                    content_callback=content_callback,
                    should_cancel=should_cancel,
                )
                or {}
            )
        return dict(self._patent_executor.execute(request=request, context=context) or {})

    def _validate_execution_result(
        self,
        *,
        request: PatentAskRequest,
        trace_id: str,
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_result = dict(execution_result or {})
        canonical_route = str(request.route or "kb_qa")
        canonical_source_scope = str(request.source_scope or "kb")
        result_route = str(normalized_result.get("route") or canonical_route)
        if result_route != canonical_route:
            raise ValueError("execution_result route must match the canonical request route")
        result_source_scope = str(normalized_result.get("source_scope") or canonical_source_scope)
        if result_source_scope != canonical_source_scope:
            raise ValueError("execution_result source_scope must match the canonical request source_scope")
        metadata = dict(normalized_result.get("metadata") or {})
        if metadata.get("success") is False:
            failed_stage = str(metadata.get("failed_stage") or "").strip()
            if not failed_stage:
                failed_stage = next(
                    (
                        str(item.get("step") or "").strip()
                        for item in reversed(list(normalized_result.get("steps") or []))
                        if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "failed"
                    ),
                    "",
                )
            detail = f" at {failed_stage}" if failed_stage else ""
            raise APIError(
                code=codes.INTERNAL_ERROR,
                message=f"patent execution failed{detail}",
                status_code=500,
                error="internal_error",
                retriable=False,
                extra={
                    "failed_stage": failed_stage,
                    "steps": [dict(item) for item in list(normalized_result.get("steps") or []) if isinstance(item, dict)],
                    "timings": dict(normalized_result.get("timings") or {}),
                },
            )
        self._result_builder.build_sync_success(
            request=request,
            trace_id=str(trace_id),
            execution_result=normalized_result,
        )
        return normalized_result
