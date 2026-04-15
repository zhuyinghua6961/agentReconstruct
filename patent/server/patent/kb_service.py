from __future__ import annotations

import inspect
import logging
from typing import Any

from server.errors import codes
from server.errors.core import APIError
from server.patent.graph_kb.models import PatentGraphKbExecutionResult
from server.patent.models import PatentQaExecutionResult
from server.patent.orchestrators.generation import PatentGenerationOrchestrator
from server.patent.pipeline import (
    build_patent_reference_instruction,
    build_retrieval_patent_result,
    build_stage3_evidence_context,
    build_stub_patent_result,
)
from server.patent.retrieval_service import PatentRetrievalService
from server.schemas.request_models import PatentAskRequest
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile

_LOGGER = logging.getLogger("patent.kb_service")


class PatentKbService:
    def __init__(
        self,
        *,
        orchestrator: PatentGenerationOrchestrator | None = None,
        retrieval_service: PatentRetrievalService | None = None,
        mode_profile: PatentModeProfile | None = None,
        runtime: Any | None = None,
        graph_kb_service: Any | None = None,
        graph_kb_client: Any | None = None,
        graph_kb_enabled: bool = False,
        graph_kb_max_rows: int = 20,
        graph_kb_timeout_ms: int = 3000,
    ) -> None:
        self._orchestrator = orchestrator or PatentGenerationOrchestrator()
        self._retrieval_service = retrieval_service
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._runtime = runtime
        self._graph_kb_service = graph_kb_service
        self._graph_kb_client = graph_kb_client
        self._graph_kb_enabled = bool(graph_kb_enabled)
        self._graph_kb_max_rows = max(1, int(graph_kb_max_rows or 20))
        self._graph_kb_timeout_ms = max(100, int(graph_kb_timeout_ms or 3000))

    def run(
        self,
        *,
        request: PatentAskRequest,
        runtime: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
        content_callback: Any | None = None,
    ) -> dict[str, Any]:
        profile = get_patent_mode_profile(request.route)
        active_runtime = runtime if runtime is not None else self._runtime
        retrieval_service = self._retrieval_service or getattr(active_runtime, "retrieval_service", None)
        _LOGGER.info(
            "patent kb_service run start trace=%s route=%s source_scope=%s staged_runtime=%s retrieval_service=%s",
            request.trace_id,
            request.route,
            request.source_scope,
            _supports_staged_runtime(active_runtime),
            retrieval_service is not None,
        )
        graph_result = self._try_graph_preflight(
            request=request,
            profile=profile,
            active_runtime=active_runtime,
            conversation_context=conversation_context,
        )
        if graph_result is not None:
            _LOGGER.info(
                "patent kb_service run completed via graph preflight trace=%s references=%s",
                request.trace_id,
                list(graph_result.get("references") or []),
            )
            return graph_result
        if _supports_staged_runtime(active_runtime):
            result = _call_with_supported_kwargs(
                self._orchestrator.run,
                question=request.question,
                runtime=active_runtime,
                conversation_context=conversation_context,
                trace_id=request.trace_id,
                progress_callback=progress_callback,
                content_callback=content_callback,
            )
            if not result.success:
                raise _build_execution_failure_error(result)
            _LOGGER.info(
                "patent kb_service run completed via staged runtime trace=%s answer_chars=%s source_ids=%s",
                request.trace_id,
                len(str(result.final_answer or "")),
                list(result.metadata.source_ids or []),
            )
            return self._execution_result_from_pipeline_result(
                request=request,
                result=result,
                profile=profile,
            )
        if retrieval_service is not None:
            retrieval_outcome = retrieval_service.retrieve(
                question=request.question,
                context=conversation_context,
            )
            _LOGGER.info(
                "patent kb_service run completed via retrieval fallback trace=%s references=%s",
                request.trace_id,
                list(retrieval_outcome.references or []),
            )
            return build_retrieval_patent_result(
                request=request,
                retrieval_outcome=retrieval_outcome,
                profile=profile,
            )
        if _requires_live_kb_backend(request):
            raise APIError(
                code=codes.SERVICE_NOT_READY,
                message="patent kb backend is not ready for file hybrid route",
                status_code=503,
                error="service_not_ready",
                retriable=True,
            )
        _LOGGER.warning("patent kb_service run falling back to stub trace=%s", request.trace_id)
        return build_stub_patent_result(
            request=request,
            context=conversation_context,
            profile=profile,
        )

    def _try_graph_preflight(
        self,
        *,
        request: PatentAskRequest,
        profile: PatentModeProfile,
        active_runtime: Any | None,
        conversation_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if str(request.route or "").strip() != "kb_qa":
            return None
        if not self._graph_kb_enabled or self._graph_kb_client is None or not callable(self._graph_kb_service):
            return None
        try:
            result = _call_with_supported_kwargs(
                self._graph_kb_service,
                question=request.question,
                conversation_context=conversation_context,
                neo4j_client=self._graph_kb_client,
                max_rows=self._graph_kb_max_rows,
                timeout_ms=self._graph_kb_timeout_ms,
                generation_runtime=active_runtime,
            )
        except Exception:
            _LOGGER.warning("patent kb_service graph preflight failed trace=%s", request.trace_id, exc_info=True)
            return None
        if not isinstance(result, PatentGraphKbExecutionResult) or not result.handled:
            return None
        return self._graph_execution_result_from_graph_result(
            request=request,
            profile=profile,
            result=result,
        )

    def _execution_result_from_pipeline_result(
        self,
        *,
        request: PatentAskRequest,
        result: PatentQaExecutionResult,
        profile: PatentModeProfile,
    ) -> dict[str, Any]:
        raw = dict(result.raw or {})
        metadata = dict(raw.get("metadata") or {})
        metadata["success"] = bool(result.success)
        metadata["source_ids"] = list(result.metadata.source_ids)
        metadata["stage1_short_circuit"] = bool(result.metadata.stage1_short_circuit or metadata.get("stage1_short_circuit"))
        metadata["stage25_skipped"] = bool(result.metadata.stage25_skipped)
        if result.metadata.stage25_skip_reason:
            metadata["stage25_skip_reason"] = str(result.metadata.stage25_skip_reason)
        metadata["kb_evidence_context"] = build_stage3_evidence_context(dict(raw.get("stage3") or {}))
        metadata["kb_reference_instruction"] = build_patent_reference_instruction(list(raw.get("references") or []))
        return {
            "answer_text": str(result.final_answer or ""),
            "route": str(profile.route),
            "query_mode": str(profile.query_mode),
            "steps": [dict(item) for item in list(raw.get("steps") or []) if isinstance(item, dict)] or _build_steps_from_metadata(result=result),
            "references": list(raw.get("references") or []),
            "reference_objects": list(raw.get("reference_objects") or []),
            "reference_links": list(raw.get("reference_links") or []),
            "original_links": list(raw.get("original_links") or []),
            "metadata": metadata,
            "timings": dict(result.metadata.stage_timings_ms or {}),
            "used_files": [],
            "file_selection": dict(request.file_selection or {}),
            "source_scope": request.source_scope,
        }

    def _graph_execution_result_from_graph_result(
        self,
        *,
        request: PatentAskRequest,
        profile: PatentModeProfile,
        result: PatentGraphKbExecutionResult,
    ) -> dict[str, Any]:
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "success": True,
                "query_mode": "patent_graph_kb",
                "template_id": str(result.template_id or ""),
                "result_count": int(result.result_count or 0),
                "source_ids": list(result.references),
            }
        )
        return {
            "answer_text": str(result.answer or ""),
            "route": str(profile.route),
            "query_mode": "patent_graph_kb",
            "steps": [
                {
                    "step": "patent_graph_kb",
                    "title": "专利图谱",
                    "message": "专利图谱：已完成结构化图谱查询",
                    "status": "success",
                }
            ],
            "references": list(result.references),
            "reference_objects": [dict(item) for item in list(result.reference_objects or []) if isinstance(item, dict)],
            "reference_links": [],
            "original_links": [],
            "metadata": metadata,
            "timings": {"patent_graph_kb": float(result.latency_ms or 0.0)},
            "used_files": [],
            "file_selection": dict(request.file_selection or {}),
            "source_scope": request.source_scope,
        }


def _supports_staged_runtime(runtime: Any | None) -> bool:
    required = (
        "stage1_pre_answer_and_planning",
        "stage2_targeted_retrieval",
        "_extract_patent_ids_from_results",
        "stage25_patent_evidence_expansion",
        "stage3_load_patent_evidence",
        "stage4_synthesis_with_patent_evidence",
    )
    return runtime is not None and all(callable(getattr(runtime, name, None)) for name in required)


def _requires_live_kb_backend(request: PatentAskRequest) -> bool:
    route = str(request.route or "").strip()
    source_scope = str(request.source_scope or "").strip()
    if route == "kb_qa":
        return False
    return "kb" in source_scope.split("+")


def _build_steps_from_metadata(*, result: PatentQaExecutionResult) -> list[dict[str, Any]]:
    timings = dict(result.metadata.stage_timings_ms or {})
    ordered = [
        ("stage1", "阶段一", "阶段一：已完成深度预回答与检索规划", "阶段一：深度预回答与检索规划失败"),
        ("stage2", "阶段二", "阶段二：已完成专利双库检索与归并", "阶段二：专利双库检索失败"),
        ("stage25", "阶段二点五", "阶段二点五：已完成MD原文扩展检索", "阶段二点五：MD原文扩展失败"),
        ("stage3", "阶段三", "阶段三：已完成专利证据与表格组装", "阶段三：专利证据组装失败"),
        ("stage4", "阶段四", "阶段四：已完成答案生成", "阶段四：答案生成失败"),
    ]
    last_completed_key = next(
        (
            key
            for key, _title, _default_message, _failure_message in reversed(ordered)
            if key in timings
        ),
        "",
    )
    steps: list[dict[str, Any]] = []
    for key, title, default_message, failure_message in ordered:
        if key not in timings and not (key == "stage25" and result.metadata.stage25_skipped):
            continue
        message = default_message
        status = "success"
        if key == "stage25" and result.metadata.stage25_skipped:
            reason = str(result.metadata.stage25_skip_reason or "").strip()
            message = "阶段二点五：已跳过MD原文扩展" if not reason else f"阶段二点五：已跳过MD原文扩展（{reason}）"
            status = "skipped"
        elif not result.success and key == last_completed_key:
            message = failure_message
            status = "failed"
        steps.append(
            {
                "step": key,
                "title": title,
                "message": message,
                "status": status,
            }
        )
    return steps


def _build_execution_failure_error(result: PatentQaExecutionResult) -> APIError:
    steps = [dict(item) for item in list(result.raw.get("steps") or []) if isinstance(item, dict)] or _build_steps_from_metadata(result=result)
    failed_step = next(
        (
            str(item.get("step") or "").strip()
            for item in reversed(steps)
            if str(item.get("status") or "").strip().lower() == "failed"
        ),
        "",
    )
    detail = f" at {failed_step}" if failed_step else ""
    return APIError(
        code=codes.INTERNAL_ERROR,
        message=f"patent staged execution failed{detail}",
        status_code=500,
        error="internal_error",
        retriable=False,
        extra={
            "failed_stage": failed_step,
            "steps": steps,
            "timings": dict(result.metadata.stage_timings_ms or {}),
        },
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
