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
        references, reference_objects = self._normalize_reference_payloads(execution_result)
        resolved_route = str(request.route or "kb_qa")
        resolved_source_scope = str(request.source_scope or "kb")
        payload = PatentSyncSuccess(
            final_answer=str(execution_result.get("answer_text") or ""),
            query_mode=self._query_mode(route=resolved_route, execution_result=execution_result),
            route=resolved_route,
            requested_mode=self._mode_profile.requested_mode,
            actual_mode=self._mode_profile.actual_mode,
            source_scope=resolved_source_scope,
            timings=dict(execution_result.get("timings") or {}),
            references=references,
            reference_objects=reference_objects,
            reference_links=self._coerce_object_list(execution_result.get("reference_links")),
            original_links=self._coerce_object_list(execution_result.get("original_links")),
            metadata=dict(execution_result.get("metadata") or {}),
            trace_id=str(trace_id),
            used_files=self._coerce_object_list(execution_result.get("used_files")),
            file_selection=dict(execution_result.get("file_selection") or {}),
        )
        return payload.model_dump()

    def build_metadata_event(
        self,
        *,
        trace_id: str,
        seq: int,
        route: str | None = None,
        query_mode: str | None = None,
        source_scope: str | None = None,
    ) -> dict[str, Any]:
        resolved_route = str(route or self._mode_profile.route)
        event = MetadataEvent(
            seq=int(seq),
            ts=self._timestamp(),
            requested_mode=self._mode_profile.requested_mode,
            actual_mode=self._mode_profile.actual_mode,
            route=resolved_route,
            query_mode=str(query_mode or get_patent_mode_profile(resolved_route).query_mode),
            source_scope=str(source_scope or "kb"),
            metadata={},
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
            yield self.build_step_event(seq=seq, step=step)
            seq += 1

        answer_text = str(execution_result.get("answer_text") or "")
        if answer_text:
            yield self.build_content_event(seq=seq, content=answer_text)

    def build_step_event(self, *, seq: int, step: dict[str, Any]) -> dict[str, Any]:
        payload = dict(step or {})
        return StepEvent(
            seq=int(seq),
            ts=self._timestamp(),
            step=str(payload.get("step") or "").strip() or None,
            title=str(payload.get("title") or "").strip() or None,
            message=str(payload.get("message") or "").strip() or None,
            detail=str(payload.get("detail") or "").strip() or None,
            status=str(payload.get("status") or "").strip() or None,
            error=str(payload.get("error") or "").strip() or None,
            data=dict(payload.get("data") or {}) if isinstance(payload.get("data"), dict) else None,
        ).model_dump()

    def build_content_event(
        self,
        *,
        seq: int,
        content: str,
        content_role: str | None = None,
        content_source: str | None = None,
        content_stream_id: str | None = None,
        content_phase: str | None = None,
        replace_stream: bool | None = None,
    ) -> dict[str, Any]:
        return ContentEvent(
            seq=int(seq),
            ts=self._timestamp(),
            content=str(content or ""),
            content_role=content_role,
            content_source=content_source,
            content_stream_id=content_stream_id,
            content_phase=content_phase,
            replace_stream=replace_stream,
        ).model_dump(exclude_none=True)

    def build_done_event(
        self,
        *,
        request: PatentAskRequest,
        trace_id: str,
        execution_result: dict[str, Any],
        seq: int,
    ) -> dict[str, Any]:
        references, reference_objects = self._normalize_reference_payloads(execution_result)
        resolved_route = str(request.route or "kb_qa")
        resolved_source_scope = str(request.source_scope or "kb")
        event = DoneEvent(
            seq=int(seq),
            ts=self._timestamp(),
            final_answer=str(execution_result.get("answer_text") or ""),
            query_mode=self._query_mode(route=resolved_route, execution_result=execution_result),
            route=resolved_route,
            requested_mode=self._mode_profile.requested_mode,
            actual_mode=self._mode_profile.actual_mode,
            source_scope=resolved_source_scope,
            timings=dict(execution_result.get("timings") or {}),
            references=references,
            reference_objects=reference_objects,
            reference_links=self._coerce_object_list(execution_result.get("reference_links")),
            original_links=self._coerce_object_list(execution_result.get("original_links")),
            metadata=dict(execution_result.get("metadata") or {}),
            trace_id=str(trace_id),
            used_files=self._coerce_object_list(execution_result.get("used_files")),
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
        if type(exc).__name__ == "_PatentStreamCancelled":
            return APIError(
                code="ASK_CANCELLED",
                message="cancelled",
                status_code=499,
                error="cancelled",
                retriable=False,
            )
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

    def _query_mode(self, *, route: str, execution_result: dict[str, Any]) -> str:
        raw_query_mode = str(execution_result.get("query_mode") or "").strip()
        if raw_query_mode and raw_query_mode != "patent":
            return raw_query_mode
        return str(get_patent_mode_profile(route).query_mode)

    def _normalize_reference_payloads(self, execution_result: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
        references = self._coerce_references(execution_result.get("references"))
        reference_objects = self._coerce_object_list(execution_result.get("reference_objects"))
        if not reference_objects:
            return references, reference_objects

        derived_references = self._derive_references_from_objects(reference_objects)
        if references and references != derived_references:
            raise ValueError("references must match reference_objects canonical patent identifiers")
        return derived_references, reference_objects

    @staticmethod
    def _coerce_steps(value: Iterable[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in value or []:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "step": str(item.get("step") or "").strip() or None,
                    "title": str(item.get("title") or "").strip() or None,
                    "message": str(item.get("message") or "").strip() or None,
                    "detail": str(item.get("detail") or "").strip() or None,
                    "status": str(item.get("status") or "").strip() or None,
                    "error": str(item.get("error") or "").strip() or None,
                    "data": dict(item.get("data") or {}) if isinstance(item.get("data"), dict) else None,
                }
            )
        return normalized

    @staticmethod
    def _coerce_references(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("references must be a list")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("references items must be strings")
            normalized.append(item.strip())
        return normalized

    @staticmethod
    def _coerce_object_list(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("object-list fields must be lists")
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("object-list items must be objects")
            normalized.append(dict(item))
        return normalized

    @staticmethod
    def _derive_references_from_objects(reference_objects: list[dict[str, Any]]) -> list[str]:
        normalized: list[str] = []
        for item in reference_objects:
            canonical_patent_id = str(item.get("canonical_patent_id") or "").strip()
            if not canonical_patent_id:
                raise ValueError("reference_objects items must include canonical_patent_id")
            normalized.append(canonical_patent_id)
        return normalized
