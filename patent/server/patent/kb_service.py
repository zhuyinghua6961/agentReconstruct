from __future__ import annotations

import inspect
import logging
from typing import Any

from server.errors import codes
from server.errors.core import APIError
from server.patent.models import PatentQaExecutionResult
from server.patent.orchestrators.generation import PatentGenerationOrchestrator
from server.patent.pipeline import build_retrieval_patent_result, build_stub_patent_result
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
    ) -> None:
        self._orchestrator = orchestrator or PatentGenerationOrchestrator()
        self._retrieval_service = retrieval_service
        self._mode_profile = mode_profile or get_patent_mode_profile()
        self._runtime = runtime

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
        _LOGGER.warning("patent kb_service run falling back to stub trace=%s", request.trace_id)
        return build_stub_patent_result(
            request=request,
            context=conversation_context,
            profile=profile,
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
