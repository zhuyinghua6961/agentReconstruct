from __future__ import annotations

import inspect
import logging
from typing import Any

from server.errors import codes
from server.errors.core import APIError
from server.patent.file_contract import build_patent_file_contract
from server.patent.file_routes import (
    _has_usable_hybrid_evidence,
    build_patent_hybrid_synthesis_contract,
    dispatch_patent_file_route,
    synthesize_patent_hybrid_answer,
)
from server.patent.kb_service import PatentKbService
from server.patent.orchestrators.generation import PatentGenerationOrchestrator
from server.patent.pdf_service import PatentPdfService
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.stream_events import (
    PatentStructuredContentRouter,
    PatentFinalContentStreamEmitter,
    final_content_source_for_route,
    preview_streaming_enabled,
    structured_content_streaming_enabled,
)
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


def _file_route_cache_metadata(metadata: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    namespace = str(metadata.get("cache_namespace") or "").strip()
    fingerprint = str(metadata.get("cache_fingerprint") or "").strip()
    cache_hit = bool(metadata.get("cache_hit")) or bool(payload.get("cache_hit"))
    if namespace != "file-route" and not fingerprint and "cache_hit" not in metadata and "cache_hit" not in payload:
        return {}
    return {
        "file_route_cache_hit": cache_hit,
        "file_route_cache_namespace": namespace,
        "file_route_cache_fingerprint": fingerprint,
    }


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
        execution_cache: Any | None = None,
        runtime_required: bool = False,
    ) -> None:
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._runtime = runtime
        self._execution_cache = execution_cache
        self._runtime_required = bool(runtime_required)
        self._kb_service = kb_service or PatentKbService(
            orchestrator=PatentGenerationOrchestrator(execution_cache=execution_cache),
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
        structured_file_streaming = structured_content_streaming_enabled(
            options=request.options,
            route=request.route,
        )
        preview_file_streaming = preview_streaming_enabled(
            options=request.options,
            route=request.route,
            source_scope=request.source_scope,
        )
        structured_router: PatentStructuredContentRouter | None = None
        final_stream_emitter: PatentFinalContentStreamEmitter | None = None
        preview_stream_emitter: Any | None = None
        forwarded_content_callback = None if contract.includes_kb else content_callback
        if callable(content_callback) and structured_file_streaming:
            structured_router = PatentStructuredContentRouter(callback=content_callback)
        if (
            structured_router is not None
            and preview_file_streaming
        ):
            if request.source_scope in {"pdf+table", "pdf+table+kb"}:
                forwarded_content_callback = structured_router
            elif request.source_scope == "pdf+kb":
                preview_stream_emitter = structured_router.preview_emitter(
                    content_source="pdf",
                    content_stream_id="pdf:primary",
                )
                forwarded_content_callback = preview_stream_emitter
            elif request.source_scope == "table+kb":
                preview_stream_emitter = structured_router.preview_emitter(
                    content_source="table",
                    content_stream_id="table:selected",
                )
                forwarded_content_callback = preview_stream_emitter
        if (
            structured_router is not None
            and not preview_file_streaming
            and not contract.includes_kb
        ):
            final_stream_emitter = structured_router.final_emitter(
                content_source=final_content_source_for_route(request.route),
            )
            forwarded_content_callback = final_stream_emitter
        _LOGGER.info(
            "patent file-route start trace=%s route=%s source_scope=%s handler_scope_kb=%s content_callback=%s forwarded_content_callback=%s",
            request.trace_id,
            request.route,
            request.source_scope,
            contract.includes_kb,
            callable(content_callback),
            callable(forwarded_content_callback),
        )
        file_route_succeeded = False
        try:
            file_result = dispatch_patent_file_route(
                contract=contract,
                pdf_service=self._pdf_service,
                tabular_service=self._tabular_service,
                execution_cache=self._execution_cache,
                progress_callback=progress_callback,
                content_callback=forwarded_content_callback,
            )
            file_route_succeeded = True
        finally:
            if final_stream_emitter is not None:
                if file_route_succeeded:
                    final_stream_emitter.close()
                else:
                    final_stream_emitter.abort()
            if preview_stream_emitter is not None:
                if file_route_succeeded:
                    preview_stream_emitter.close()
                else:
                    preview_stream_emitter.abort()
        _LOGGER.info(
            "patent file-route result trace=%s route=%s handler=%s answer_chars=%s cache_hit=%s",
            request.trace_id,
            request.route,
            file_result.get("handler"),
            len(str(file_result.get("answer_text") or "")),
            bool(dict(file_result.get("metadata") or {}).get("cache_hit")),
        )
        if not contract.includes_kb:
            return file_result

        if callable(progress_callback):
            progress_callback(
                {
                    "step": "kb_evidence",
                    "title": "加载知识库证据",
                    "message": "🧠 正在加载 patent 知识库证据...",
                    "status": "running",
                }
            )
        kb_result = _call_with_supported_kwargs(
            self._kb_service.run,
            request=request,
            runtime=self._runtime,
            conversation_context=context,
            progress_callback=progress_callback,
            content_callback=None,
        )
        if callable(progress_callback):
            progress_callback(
                {
                    "step": "kb_evidence",
                    "title": "加载知识库证据",
                    "message": "🧠 已完成 patent 知识库证据加载",
                    "status": "success",
                }
            )
            progress_callback(
                {
                    "step": "hybrid_answer",
                    "title": "统一合成答案",
                    "message": "🧩 正在统一合成文件与知识库答案...",
                    "status": "running",
                }
            )
        merged = self._merge_file_and_kb_results(file_result=file_result, kb_result=kb_result, source_scope=request.source_scope)
        final_hybrid_step = next(
            (
                dict(item)
                for item in reversed(list(merged.get("steps") or []))
                if isinstance(item, dict) and str(item.get("step") or "").strip() == "hybrid_answer"
            ),
            {"step": "hybrid_answer", "title": "统一合成答案", "message": "🧩 已完成文件与知识库统一合成", "status": "success"},
        )
        if callable(content_callback):
            if structured_router is not None and preview_file_streaming:
                merged_final_emitter = structured_router.final_emitter(content_source="hybrid")
                merged_final_succeeded = False
                try:
                    emitted = emit_text_chunks(str(merged.get("answer_text") or ""), content_callback=merged_final_emitter)
                    merged_final_succeeded = True
                finally:
                    if merged_final_succeeded:
                        merged_final_emitter.close()
                    else:
                        merged_final_emitter.abort()
            else:
                emitted = emit_text_chunks(str(merged.get("answer_text") or ""), content_callback=content_callback)
            _LOGGER.info(
                "patent file-route merged stream trace=%s route=%s source_scope=%s emitted_chunks=%s answer_chars=%s",
                request.trace_id,
                request.route,
                request.source_scope,
                emitted,
                len(str(merged.get("answer_text") or "")),
            )
        if callable(progress_callback):
            progress_callback(dict(final_hybrid_step))
        return merged

    @staticmethod
    def _merge_file_and_kb_results(
        *,
        file_result: dict[str, Any],
        kb_result: dict[str, Any],
        source_scope: str,
    ) -> dict[str, Any]:
        merged = dict(file_result or {})
        kb_payload = dict(kb_result or {})
        file_answer = str(merged.get("answer_text") or "").strip()
        kb_answer = str(kb_payload.get("answer_text") or "").strip()
        file_metadata = dict(merged.get("metadata") or {})
        kb_metadata = dict(kb_payload.get("metadata") or {})
        synthesis_contract = build_patent_hybrid_synthesis_contract(
            question="",
            source_scope=source_scope,
            pdf_answer="" if merged.get("handler") == "tabular" else file_answer,
            tabular_answer=file_answer if merged.get("handler") in {"tabular", "hybrid"} else "",
            pdf_evidence_context=str(file_metadata.get("pdf_evidence_context") or ""),
            table_execution_context=str(file_metadata.get("table_evidence_context") or ""),
            kb_answer=kb_answer,
            include_kb=True,
            kb_evidence_context=str(kb_metadata.get("kb_evidence_context") or ""),
            kb_reference_instruction=str(kb_metadata.get("kb_reference_instruction") or ""),
        )
        if merged.get("handler") == "hybrid":
            existing_contract = file_metadata.get("synthesis_contract")
            if isinstance(existing_contract, dict):
                synthesis_contract.update(
                    {
                        "question": str(existing_contract.get("question") or ""),
                        "source_scope": str(existing_contract.get("source_scope") or source_scope),
                        "pdf_answer": str(existing_contract.get("pdf_answer") or ""),
                        "tabular_answer": str(existing_contract.get("tabular_answer") or ""),
                        "pdf_evidence_context": str(existing_contract.get("pdf_evidence_context") or ""),
                        "table_execution_context": str(existing_contract.get("table_execution_context") or ""),
                    }
                )
        merged["answer_text"] = synthesize_patent_hybrid_answer(synthesis_contract=synthesis_contract)
        hybrid_success = _has_usable_hybrid_evidence(synthesis_contract=synthesis_contract)
        prior_steps = [
            dict(item)
            for item in list(merged.get("steps") or [])
            if isinstance(item, dict) and str(item.get("step") or "").strip() != "hybrid_answer"
        ]
        merged["steps"] = [
            *prior_steps,
            *[dict(item) for item in list(kb_payload.get("steps") or []) if isinstance(item, dict)],
            {"step": "kb_evidence", "title": "加载知识库证据", "message": "🧠 已完成 patent 知识库证据加载", "status": "success"},
            {
                "step": "hybrid_answer",
                "title": "统一合成答案",
                "message": "🧩 已完成文件与知识库统一合成" if hybrid_success else "🧩 文件与知识库统一合成失败：当前没有可用于联合回答的证据",
                "status": "success" if hybrid_success else "error",
            },
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
            **file_metadata,
            **kb_metadata,
            **_file_route_cache_metadata(file_metadata, merged),
            "kb_participated": True,
            "answer_mode": "hybrid_unified_synthesis",
            "synthesis_contract": dict(synthesis_contract),
            "steps": [dict(item) for item in merged["steps"]],
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
