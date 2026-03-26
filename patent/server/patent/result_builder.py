from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

from server.errors import codes
from server.errors.core import APIError
from server.schemas.request_models import PatentAskRequest
from server.schemas.response_models import (
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    MetadataEvent,
    PatentSyncSuccess,
    StepEvent,
)
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


TimestampFactory = Callable[[], str]



def default_now_factory() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PatentResultBuilder:
    def __init__(
        self,
        *,
        mode_profile: PatentModeProfile | None = None,
        now_factory: TimestampFactory | None = None,
    ) -> None:
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._now_factory = now_factory or default_now_factory

    def build_sync_success(
        self,
        *,
        request: PatentAskRequest,
        trace_id: str,
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = PatentSyncSuccess(
            data={
                "final_answer": str(execution_result.get("answer_text") or ""),
                "timings": dict(execution_result.get("timings") or {}),
                "metadata": {
                    "requested_mode": self._mode_profile.requested_mode,
                    "actual_mode": self._mode_profile.actual_mode,
                    "route": str(execution_result.get("route") or self._mode_profile.route),
                    "mode": self._mode_profile.actual_mode,
                    "query_mode": str(execution_result.get("query_mode") or self._mode_profile.query_mode),
                    "conversation_id": request.conversation_id,
                },
                "references": list(execution_result.get("references") or []),
                "pdf_links": list(execution_result.get("pdf_links") or []),
                "reference_links": list(execution_result.get("reference_links") or []),
                "trace_id": str(trace_id),
            },
            trace_id=str(trace_id),
        )
        return payload.model_dump()

    def build_metadata_event(
        self,
        *,
        trace_id: str,
        seq: int,
        route: str | None = None,
        query_mode: str | None = None,
    ) -> dict[str, Any]:
        event = MetadataEvent(
            seq=int(seq),
            ts=self._timestamp(),
            requested_mode=self._mode_profile.requested_mode,
            actual_mode=self._mode_profile.actual_mode,
            route=str(route or self._mode_profile.route),
            query_mode=str(query_mode or self._mode_profile.query_mode),
            trace_id=str(trace_id),
        )
        return event.model_dump()

    def iter_progress_events(
        self,
        *,
        execution_result: dict[str, Any],
        starting_seq: int,
    ) -> Iterator[dict[str, Any]]:
        seq = int(starting_seq)
        for step in self._coerce_steps(execution_result.get("steps") or []):
            yield StepEvent(
                seq=seq,
                ts=self._timestamp(),
                title=step.get("title"),
                message=step.get("message"),
            ).model_dump()
            seq += 1

        answer_text = str(execution_result.get("answer_text") or "")
        if answer_text:
            yield ContentEvent(
                seq=seq,
                ts=self._timestamp(),
                content=answer_text,
            ).model_dump()

    def build_done_event(
        self,
        *,
        trace_id: str,
        execution_result: dict[str, Any],
        seq: int,
    ) -> dict[str, Any]:
        event = DoneEvent(
            seq=int(seq),
            ts=self._timestamp(),
            final_answer=str(execution_result.get("answer_text") or ""),
            timings=dict(execution_result.get("timings") or {}),
            references=list(execution_result.get("references") or []),
            trace_id=str(trace_id),
            used_files=list(execution_result.get("used_files") or []),
            reference_links=list(execution_result.get("reference_links") or []),
            pdf_links=list(execution_result.get("pdf_links") or []),
            file_selection=dict(execution_result.get("file_selection") or {}),
        )
        return event.model_dump()

    def build_error_event(self, *, trace_id: str, seq: int, error: APIError) -> dict[str, Any]:
        event = ErrorEvent(
            seq=int(seq),
            ts=self._timestamp(),
            code=str(error.code),
            error=str(error.error),
            message=str(error.message),
            trace_id=str(trace_id),
        )
        return event.model_dump()

    def to_api_error(self, exc: Exception) -> APIError:
        if isinstance(exc, APIError):
            return exc
        if isinstance(exc, TimeoutError):
            return APIError(
                code=codes.INTERNAL_ERROR,
                message="patent execution timed out",
                status_code=504,
                error="timeout",
                retriable=True,
            )
        return APIError(
            code=codes.INTERNAL_ERROR,
            message="internal server error",
            status_code=500,
            error="internal_error",
            retriable=False,
        )

    def _timestamp(self) -> str:
        return str(self._now_factory())

    @staticmethod
    def _coerce_steps(value: Iterable[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in value or []:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "title": str(item.get("title") or "").strip() or None,
                "message": str(item.get("message") or "").strip() or None,
            })
        return normalized
