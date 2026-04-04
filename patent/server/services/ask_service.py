from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Iterator

from server.errors import codes
from server.errors.core import APIError
from server.patent.executor import PatentExecutor
from server.patent.result_builder import PatentResultBuilder, default_now_factory
from server.runtime.request_context import clear_trace_id, set_trace_id
from server.schemas.request_models import PatentAskRequest
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


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
        try:
            self._logger.info("sync_ask start trace=%s durable=%s", request.trace_id, request.is_durable)
            prepared = self._prepare_turn(request=request, user_id=user_id)
            try:
                turn_result = self._complete_turn(request=request, prepared_turn=prepared)
                self._logger.info(
                    "sync_ask complete trace=%s answer_chars=%s",
                    turn_result.get("trace_id") or request.trace_id,
                    len(str(dict(turn_result.get("execution_result") or {}).get("answer_text") or "")),
                )
                return self._result_builder.build_sync_success(
                    request=request,
                    trace_id=str(turn_result.get("trace_id") or prepared.get("trace_id") or request.trace_id),
                    execution_result=dict(turn_result.get("execution_result") or {}),
                )
            except Exception as exc:
                self._logger.exception("sync_ask failed trace=%s error=%s", request.trace_id, exc)
                self._abort_turn(prepared)
                raise self._result_builder.to_api_error(exc) from exc
        finally:
            clear_trace_id(trace_token)

    def stream_ask(self, request: PatentAskRequest, *, user_id: int | None) -> Iterator[dict[str, Any]]:
        trace_token = set_trace_id(str(request.trace_id))
        prepared: dict[str, Any] = {}
        trace_id = str(request.trace_id)
        seq = 0

        try:
            prepared = self._prepare_turn(request=request, user_id=user_id)
            trace_id = str(prepared.get("trace_id") or trace_id)
            yield self._result_builder.build_metadata_event(
                trace_id=trace_id,
                seq=seq,
                route=request.route,
                query_mode=get_patent_mode_profile(request.route).query_mode,
                source_scope=request.source_scope,
            )
            seq += 1
            if prepared.get("assistant_accept_skipped") and isinstance(prepared.get("execution_result"), dict):
                execution_result = self._validate_execution_result(
                    request=request,
                    trace_id=trace_id,
                    execution_result=dict(prepared.get("execution_result") or {}),
                )
                progress_events = list(
                    self._result_builder.iter_progress_events(
                        execution_result=execution_result,
                        starting_seq=seq,
                    )
                )
                for event in progress_events:
                    yield event
                seq += len(progress_events)
                self._ensure_done_allowed(prepared)
                yield self._result_builder.build_done_event(
                    request=request,
                    trace_id=trace_id,
                    execution_result=execution_result,
                    seq=seq,
                )
                return

            progress_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            streamed_step_count = 0
            streamed_content_count = 0

            def _progress_callback(step: dict[str, Any]) -> None:
                progress_queue.put(("progress", dict(step or {})))

            def _content_callback(chunk: str) -> None:
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
                event_type, payload = progress_queue.get()
                if event_type == "progress":
                    yield self._result_builder.build_step_event(seq=seq, step=dict(payload or {}))
                    seq += 1
                    streamed_step_count += 1
                    continue
                if event_type == "content":
                    chunk = str(payload or "")
                    if chunk:
                        yield self._result_builder.build_content_event(seq=seq, content=chunk)
                        seq += 1
                        streamed_content_count += 1
                    continue
                if event_type == "exception":
                    raise payload
                if event_type == "result":
                    execution_result = dict(payload or {})
                    break

            if streamed_step_count == 0:
                for event in self._result_builder.iter_progress_events(
                    execution_result=execution_result,
                    starting_seq=seq,
                ):
                    if streamed_content_count > 0 and str(event.get("type") or "") == "content":
                        continue
                    yield event
                    seq += 1
            else:
                answer_text = str(execution_result.get("answer_text") or "")
                if answer_text and streamed_content_count == 0:
                    yield self._result_builder.build_content_event(seq=seq, content=answer_text)
                    seq += 1

            turn_result = self._finalize_turn(
                request=request,
                prepared_turn=prepared,
                execution_result=execution_result,
            )
            trace_id = str(turn_result.get("trace_id") or trace_id)
            self._ensure_done_allowed(turn_result)
            yield self._result_builder.build_done_event(
                request=request,
                trace_id=trace_id,
                execution_result=dict(turn_result.get("execution_result") or {}),
                seq=seq,
            )
        except Exception as exc:
            self._logger.exception("stream_ask failed trace=%s error=%s", trace_id, exc)
            self._abort_turn(prepared)
            yield self._build_error_event(trace_id=trace_id, seq=seq, exc=exc)
        finally:
            clear_trace_id(trace_token)

    def _prepare_turn(self, *, request: PatentAskRequest, user_id: int | None) -> dict[str, Any]:
        prepare_turn = getattr(self._persistence_service, "prepare_turn", None)
        if callable(prepare_turn):
            return dict(prepare_turn(request=request, user_id=user_id) or {})
        fallback_result = dict(
            self._persistence_service.run_turn(
                request=request,
                user_id=user_id,
                execute_turn=lambda context: self._validate_execution_result(
                    request=request,
                    trace_id=str(request.trace_id),
                    execution_result=self._execute_turn(request=request, context=context),
                ),
            )
            or {}
        )
        fallback_result["_completed_turn"] = True
        return fallback_result

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
        if prepared_turn.get("_completed_turn") and isinstance(prepared_turn.get("execution_result"), dict):
            execution_result = self._validate_execution_result(
                request=request,
                trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
                execution_result=dict(prepared_turn.get("execution_result") or {}),
            )
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": execution_result,
            }
        if prepared_turn.get("assistant_accept_skipped") and isinstance(prepared_turn.get("execution_result"), dict):
            execution_result = self._validate_execution_result(
                request=request,
                trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
                execution_result=dict(prepared_turn.get("execution_result") or {}),
            )
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": execution_result,
            }
        execution_result = self._validate_execution_result(
            request=request,
            trace_id=str(prepared_turn.get("trace_id") or request.trace_id),
            execution_result=self._execute_turn(request=request, context=dict(prepared_turn.get("context") or {})),
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
    ) -> dict[str, Any]:
        execute_with_progress = getattr(self._patent_executor, "execute_with_progress", None)
        if callable(execute_with_progress):
            return dict(
                execute_with_progress(
                    request=request,
                    context=context,
                    progress_callback=progress_callback,
                    content_callback=content_callback,
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
            )
        self._result_builder.build_sync_success(
            request=request,
            trace_id=str(trace_id),
            execution_result=normalized_result,
        )
        return normalized_result
