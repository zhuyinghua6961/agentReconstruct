from __future__ import annotations

from typing import Any, Callable, Iterator

from server.errors import codes
from server.errors.core import APIError
from server.patent.executor import PatentExecutor
from server.patent.result_builder import PatentResultBuilder, default_now_factory
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
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._patent_executor = patent_executor or PatentExecutor(mode_profile=self._mode_profile)
        self._persistence_service = persistence_service
        self._result_builder = PatentResultBuilder(
            mode_profile=self._mode_profile,
            now_factory=now_factory or default_now_factory,
        )

    def sync_ask(self, request: PatentAskRequest, *, user_id: int | None) -> dict[str, Any]:
        prepared = self._prepare_turn(request=request, user_id=user_id)
        try:
            turn_result = self._complete_turn(request=request, prepared_turn=prepared)
            return self._result_builder.build_sync_success(
                request=request,
                trace_id=str(turn_result.get("trace_id") or prepared.get("trace_id") or request.trace_id),
                execution_result=dict(turn_result.get("execution_result") or {}),
            )
        except Exception as exc:
            self._abort_turn(prepared)
            raise self._result_builder.to_api_error(exc) from exc

    def stream_ask(self, request: PatentAskRequest, *, user_id: int | None) -> Iterator[dict[str, Any]]:
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
                query_mode=request.actual_mode,
            )
            seq += 1
            if prepared.get("assistant_accept_skipped") and isinstance(prepared.get("execution_result"), dict):
                execution_result = dict(prepared.get("execution_result") or {})
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
                    trace_id=trace_id,
                    execution_result=execution_result,
                    seq=seq,
                )
                return

            execution_result = self._execute_turn(request=request, context=dict(prepared.get("context") or {}))
            progress_events = list(
                self._result_builder.iter_progress_events(
                    execution_result=execution_result,
                    starting_seq=seq,
                )
            )
            for event in progress_events:
                yield event
            seq += len(progress_events)

            turn_result = self._finalize_turn(
                request=request,
                prepared_turn=prepared,
                execution_result=execution_result,
            )
            trace_id = str(turn_result.get("trace_id") or trace_id)
            self._ensure_done_allowed(turn_result)
            yield self._result_builder.build_done_event(
                trace_id=trace_id,
                execution_result=dict(turn_result.get("execution_result") or {}),
                seq=seq,
            )
        except Exception as exc:
            self._abort_turn(prepared)
            yield self._build_error_event(trace_id=trace_id, seq=seq, exc=exc)

    def _prepare_turn(self, *, request: PatentAskRequest, user_id: int | None) -> dict[str, Any]:
        prepare_turn = getattr(self._persistence_service, "prepare_turn", None)
        if callable(prepare_turn):
            return dict(prepare_turn(request=request, user_id=user_id) or {})
        fallback_result = dict(
            self._persistence_service.run_turn(
                request=request,
                user_id=user_id,
                execute_turn=lambda context: self._execute_turn(request=request, context=context),
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
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": dict(prepared_turn.get("execution_result") or {}),
            }
        if prepared_turn.get("assistant_accept_skipped") and isinstance(prepared_turn.get("execution_result"), dict):
            self._ensure_done_allowed(prepared_turn)
            return {
                **dict(prepared_turn or {}),
                "execution_result": dict(prepared_turn.get("execution_result") or {}),
            }
        execution_result = self._execute_turn(request=request, context=dict(prepared_turn.get("context") or {}))
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

    def _execute_turn(self, *, request: PatentAskRequest, context: dict[str, Any]) -> dict[str, Any]:
        return dict(self._patent_executor.execute(request=request, context=context) or {})
