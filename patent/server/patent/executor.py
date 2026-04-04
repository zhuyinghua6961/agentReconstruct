from __future__ import annotations

import inspect
import logging
from typing import Any

from server.errors import codes
from server.errors.core import APIError
from server.patent.file_contract import build_patent_file_contract
from server.patent.file_routes import dispatch_patent_file_route
from server.patent.kb_service import PatentKbService
from server.patent.pdf_service import PatentPdfService
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.streaming import emit_text_chunks
from server.patent.tabular_service import PatentTabularService
from server.schemas.request_models import PatentAskRequest
from server.services.conversation_context_builder import (
    build_patent_conversation_context,
    normalize_patent_conversation_context,
)
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


_FILE_ROUTES = {"pdf_qa", "tabular_qa", "hybrid_qa"}
_LOGGER = logging.getLogger("patent.executor")


class PatentExecutor:
    def __init__(
        self,
        *,
        mode_profile: PatentModeProfile | None = None,
        retrieval_service: PatentRetrievalService | None = None,
        kb_service: PatentKbService | None = None,
        pdf_service: PatentPdfService | None = None,
        tabular_service: PatentTabularService | None = None,
        runtime: Any | None = None,
        runtime_required: bool = False,
    ) -> None:
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._runtime = runtime
        self._runtime_required = bool(runtime_required)
        self._kb_service = kb_service or PatentKbService(
            retrieval_service=retrieval_service,
            mode_profile=self._mode_profile,
            runtime=runtime,
        )
        self._pdf_service = pdf_service or PatentPdfService()
        self._tabular_service = tabular_service or PatentTabularService()

    def execute(self, *, request: PatentAskRequest, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.execute_with_progress(
            request=request,
            context=context,
            progress_callback=None,
            content_callback=None,
        )

    def execute_with_progress(
        self,
        *,
        request: PatentAskRequest,
        context: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
        content_callback: Any | None = None,
    ) -> dict[str, Any]:
        self._ensure_runtime_ready(request=request)
        _LOGGER.info(
            "patent executor execute trace=%s route=%s source_scope=%s kb_enabled=%s selected_file_ids=%s",
            request.trace_id,
            request.route,
            request.source_scope,
            request.kb_enabled,
            list(request.selected_file_ids or []),
        )
        if str(request.route or "") in _FILE_ROUTES:
            normalized_context = self._normalize_context(request=request, context=context)
            _LOGGER.info("patent executor dispatching file route trace=%s route=%s", request.trace_id, request.route)
            return self._execute_file_route(
                request=request,
                context=normalized_context,
                progress_callback=progress_callback,
                content_callback=content_callback,
            )
        normalized_context = self._normalize_context(request=request, context=context)
        _LOGGER.info("patent executor dispatching kb route trace=%s", request.trace_id)
        return _call_with_supported_kwargs(
            self._kb_service.run,
            request=request,
            runtime=self._runtime,
            conversation_context=normalized_context,
            progress_callback=progress_callback,
            content_callback=content_callback,
        )

    def _execute_file_route(
        self,
        *,
        request: PatentAskRequest,
        context: dict[str, Any],
        progress_callback: Any | None = None,
        content_callback: Any | None = None,
    ) -> dict[str, Any]:
        contract = build_patent_file_contract(
            question=request.question,
            route=request.route,
            source_scope=request.source_scope,
            selected_file_ids=request.selected_file_ids,
            primary_file_id=request.primary_file_id,
            execution_files=request.execution_files,
            file_selection=request.file_selection,
            kb_enabled=request.kb_enabled,
            allow_kb_verification=request.allow_kb_verification,
        )
        file_result = dispatch_patent_file_route(
            contract=contract,
            pdf_service=self._pdf_service,
            tabular_service=self._tabular_service,
            progress_callback=progress_callback,
            content_callback=content_callback,
        )
        if not contract.includes_kb:
            return file_result
        file_answer = str(file_result.get("answer_text") or "").strip()
        kb_stream_state = {"count": 0, "prefix": False}

        def _kb_content_callback(chunk: str) -> None:
            text = str(chunk or "")
            if not text or not callable(content_callback):
                return
            if file_answer and not kb_stream_state["prefix"]:
                emit_text_chunks("\n\nPatent KB participation: ", content_callback=content_callback)
                kb_stream_state["prefix"] = True
            content_callback(text)
            kb_stream_state["count"] += 1

        kb_result = _call_with_supported_kwargs(
            self._kb_service.run,
            request=request,
            runtime=self._runtime,
            conversation_context=context,
            progress_callback=progress_callback,
            content_callback=_kb_content_callback if callable(content_callback) else content_callback,
        )
        kb_answer = str(kb_result.get("answer_text") or "").strip()
        if callable(content_callback) and kb_answer and kb_stream_state["count"] == 0:
            if file_answer and not kb_stream_state["prefix"]:
                emit_text_chunks("\n\nPatent KB participation: ", content_callback=content_callback)
                kb_stream_state["prefix"] = True
            emit_text_chunks(kb_answer, content_callback=content_callback)
        return self._merge_file_and_kb_results(file_result=file_result, kb_result=kb_result)

    @staticmethod
    def _merge_file_and_kb_results(
        *,
        file_result: dict[str, Any],
        kb_result: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(file_result or {})
        kb_payload = dict(kb_result or {})
        file_answer = str(merged.get("answer_text") or "").strip()
        kb_answer = str(kb_payload.get("answer_text") or "").strip()
        if kb_answer:
            merged["answer_text"] = kb_answer if not file_answer else f"{file_answer}\n\nPatent KB participation: {kb_answer}"
        merged["steps"] = [
            *[dict(item) for item in list(merged.get("steps") or []) if isinstance(item, dict)],
            *[dict(item) for item in list(kb_payload.get("steps") or []) if isinstance(item, dict)],
        ]
        merged["references"] = [
            str(item).strip()
            for item in list(kb_payload.get("references") or [])
            if str(item).strip()
        ]
        merged["reference_objects"] = [
            dict(item)
            for item in list(kb_payload.get("reference_objects") or [])
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip()
        ]
        merged["reference_links"] = PatentExecutor._merge_list_values(
            merged.get("reference_links"),
            kb_payload.get("reference_links"),
        )
        merged["original_links"] = PatentExecutor._merge_list_values(
            merged.get("original_links"),
            kb_payload.get("original_links"),
        )
        merged["metadata"] = {
            **dict(merged.get("metadata") or {}),
            **dict(kb_payload.get("metadata") or {}),
            "kb_participated": True,
        }
        merged["timings"] = {
            **dict(merged.get("timings") or {}),
            **dict(kb_payload.get("timings") or {}),
        }
        return merged

    @staticmethod
    def _merge_list_values(left: Any, right: Any) -> list[Any]:
        merged: list[Any] = []
        seen: set[str] = set()
        for source in (left, right):
            if not isinstance(source, list):
                continue
            for item in source:
                marker = repr(item)
                if marker in seen:
                    continue
                seen.add(marker)
                merged.append(item)
        return merged

    def _ensure_runtime_ready(self, *, request: PatentAskRequest) -> None:
        if not self._runtime_required:
            return
        if self._runtime is not None:
            return
        route = str(request.route or "")
        source_scope = str(request.source_scope or "")
        if route != "kb_qa" and "kb" not in source_scope.split("+"):
            return
        raise APIError(
            code=codes.SERVICE_NOT_READY,
            message="patent runtime is not ready",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )

    @staticmethod
    def _normalize_context(*, request: PatentAskRequest, context: dict[str, Any] | None) -> dict[str, Any]:
        raw_context = dict(context or {})
        if any(key in raw_context for key in ("recent_turns_for_llm", "summary_for_llm", "source_selection")):
            return normalize_patent_conversation_context(
                recent_turns_for_llm=raw_context.get("recent_turns_for_llm"),
                summary_for_llm=raw_context.get("summary_for_llm"),
                conversation_state=raw_context.get("conversation_state"),
                source_selection=raw_context.get("source_selection")
                if isinstance(raw_context.get("source_selection"), dict)
                else {
                    "source_scope": request.source_scope,
                    "selected_file_ids": request.selected_file_ids,
                },
            )
        return build_patent_conversation_context(
            request=request,
            raw_context=raw_context,
        )


def _call_with_supported_kwargs(fn, /, **kwargs):
    if not callable(fn):
        raise TypeError("target is not callable")
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return fn(**kwargs)
    filtered = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }
    return fn(**filtered)
