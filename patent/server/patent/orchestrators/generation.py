from __future__ import annotations

import inspect
import logging
import threading
import time
from typing import Any

from server.patent.cache_keys import (
    build_stage1_cache_fingerprint,
    build_stage2_cache_fingerprint,
    build_stage25_cache_fingerprint,
    build_stage3_cache_fingerprint,
)
from server.patent.models import PatentQaExecutionMetadata, PatentQaExecutionResult, PatentRetrievalPlan
from server.patent.models import PatentRetrievalClaim

_LOGGER = logging.getLogger("patent.generation")


def _as_retrieval_plan(value: Any) -> PatentRetrievalPlan:
    if isinstance(value, PatentRetrievalPlan):
        return value
    if isinstance(value, dict):
        return PatentRetrievalPlan(
            question_type=str(value.get("question_type") or ""),
            analysis_axes=list(value.get("analysis_axes") or []),
            explicit_patent_ids=list(value.get("explicit_patent_ids") or []),
            candidate_recall_queries=list(value.get("candidate_recall_queries") or []),
            evidence_localization_queries=list(value.get("evidence_localization_queries") or []),
            preferred_sections=list(value.get("preferred_sections") or []),
            filters=dict(value.get("filters") or {}),
        )
    return PatentRetrievalPlan()


def _as_retrieval_claims(value: Any) -> list[PatentRetrievalClaim]:
    claims: list[PatentRetrievalClaim] = []
    for item in list(value or []):
        if isinstance(item, PatentRetrievalClaim):
            claims.append(item)
            continue
        if not isinstance(item, dict):
            continue
        claims.append(
            PatentRetrievalClaim(
                claim=str(item.get("claim") or ""),
                keywords=list(item.get("keywords") or []),
                preferred_sections=list(item.get("preferred_sections") or []),
                filters=dict(item.get("filters") or {}),
            )
        )
    return [claim for claim in claims if str(claim.claim or "").strip()]


def _coalesce_stage4_list(final_payload: dict[str, Any], stage2_result: dict[str, Any], key: str) -> list[Any]:
    if key in final_payload:
        return list(final_payload.get(key) or [])
    return list(stage2_result.get(key) or [])


def _question_preview(question: str, *, limit: int = 120) -> str:
    text = " ".join(str(question or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _count_list_items(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _evidence_counts(bundle: dict[str, Any] | None) -> tuple[int, int]:
    evidence_by_patent_id = dict((bundle or {}).get("evidence_by_patent_id") or {})
    source_count = 0
    evidence_count = 0
    for items in evidence_by_patent_id.values():
        if not isinstance(items, list):
            continue
        if items:
            source_count += 1
        evidence_count += len(items)
    return source_count, evidence_count


def _callable_accepts_keyword(fn: Any, keyword: str) -> bool:
    if not callable(fn):
        return False
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == keyword:
            return True
    return False


def _emit_progress_step(
    progress_callback,
    *,
    step: str,
    title: str,
    message: str,
    status: str = "processing",
    detail: str = "",
    data: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if not callable(progress_callback):
        return
    progress_callback(
        {
            "step": str(step or "").strip(),
            "title": str(title or "").strip(),
            "message": str(message or "").strip(),
            "status": str(status or "").strip() or "processing",
            **({"detail": str(detail).strip()} if str(detail or "").strip() else {}),
            **({"error": str(error).strip()} if str(error or "").strip() else {}),
            **({"data": dict(data)} if isinstance(data, dict) and data else {}),
        }
    )


def _build_stage_steps(
    *,
    timings: dict[str, float],
    stage25_result: dict[str, Any] | None,
    success: bool,
) -> list[dict[str, Any]]:
    ordered = [
        ("stage1", "阶段一", "阶段一：已完成深度预回答与检索规划", "阶段一：深度预回答与检索规划失败"),
        ("stage2", "阶段二", "阶段二：已完成专利双库检索与归并", "阶段二：专利双库检索失败"),
        ("stage25", "阶段二点五", "阶段二点五：已完成MD原文扩展检索", "阶段二点五：MD原文扩展失败"),
        ("stage3", "阶段三", "阶段三：已完成专利证据与表格组装", "阶段三：专利证据组装失败"),
        ("stage4", "阶段四", "阶段四：已完成答案生成", "阶段四：答案生成失败"),
    ]
    resolved_stage25 = dict(stage25_result or {})
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
        if key not in timings and not (key == "stage25" and resolved_stage25.get("skipped")):
            continue
        message = default_message
        status = "success"
        if key == "stage25" and resolved_stage25.get("skipped"):
            reason = str(resolved_stage25.get("skip_reason") or "").strip()
            message = "阶段二点五：已跳过MD原文扩展" if not reason else f"阶段二点五：已跳过MD原文扩展（{reason}）"
            status = "skipped"
        elif not success and key == last_completed_key:
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


class PatentGenerationOrchestrator:
    def __init__(
        self,
        *,
        execution_cache: Any | None = None,
        stage_cache_ttl_seconds: int = 300,
        singleflight_ttl_seconds: int = 30,
        singleflight_poll_interval_seconds: float = 0.01,
        singleflight_renew_interval_seconds: float | None = None,
        singleflight_wait_timeout_seconds: float | None = None,
    ) -> None:
        self._execution_cache = execution_cache
        self._stage_cache_ttl_seconds = max(1, int(stage_cache_ttl_seconds))
        self._singleflight_ttl_seconds = max(1, int(singleflight_ttl_seconds))
        self._singleflight_poll_interval_seconds = max(0.0, float(singleflight_poll_interval_seconds))
        renew_interval = (
            min(float(self._singleflight_ttl_seconds) / 3.0, 10.0)
            if singleflight_renew_interval_seconds is None
            else float(singleflight_renew_interval_seconds)
        )
        self._singleflight_renew_interval_seconds = max(0.001, renew_interval)
        if singleflight_wait_timeout_seconds is None:
            self._singleflight_wait_timeout_seconds: float | None = None
        else:
            self._singleflight_wait_timeout_seconds = max(0.0, float(singleflight_wait_timeout_seconds))

    def _timed(self, timings: dict[str, float], key: str, fn):
        started = time.perf_counter()
        result = fn()
        timings[key] = round((time.perf_counter() - started) * 1000, 3)
        return result

    def _run_cached_stage(self, *, stage: str, fingerprint: str, compute):
        cache = self._execution_cache
        if cache is None or not bool(getattr(cache, "available", True)):
            return compute()

        try:
            cached = cache.get_stage_cache(stage=stage, fingerprint=fingerprint)
        except Exception:
            return compute()
        if cached is not None:
            return cached

        try:
            token = cache.claim_stage_singleflight(
                stage=stage,
                fingerprint=fingerprint,
                ttl_seconds=self._singleflight_ttl_seconds,
            )
        except Exception:
            return compute()
        claimed = bool(token)
        renew_stop: threading.Event | None = None
        renew_thread: threading.Thread | None = None
        renew_error: list[str] = []
        try:
            if not claimed:
                wait_timeout = (
                    float(self._singleflight_ttl_seconds)
                    if self._singleflight_wait_timeout_seconds is None
                    else float(self._singleflight_wait_timeout_seconds)
                )
                deadline = time.monotonic() + wait_timeout
                while True:
                    try:
                        cached = cache.get_stage_cache(stage=stage, fingerprint=fingerprint)
                    except Exception:
                        return compute()
                    if cached is not None:
                        return cached
                    try:
                        owner = str(
                            getattr(cache, "get_stage_singleflight_owner", lambda **_kwargs: "")(
                                stage=stage,
                                fingerprint=fingerprint,
                            )
                            or ""
                        ).strip()
                    except Exception:
                        return compute()
                    if not owner:
                        try:
                            token = cache.claim_stage_singleflight(
                                stage=stage,
                                fingerprint=fingerprint,
                                ttl_seconds=self._singleflight_ttl_seconds,
                            )
                        except Exception:
                            return compute()
                        claimed = bool(token)
                        if claimed:
                            break
                        try:
                            cached = cache.get_stage_cache(stage=stage, fingerprint=fingerprint)
                        except Exception:
                            return compute()
                        if cached is not None:
                            return cached
                        try:
                            owner = str(
                                getattr(cache, "get_stage_singleflight_owner", lambda **_kwargs: "")(
                                    stage=stage,
                                    fingerprint=fingerprint,
                                )
                                or ""
                            ).strip()
                        except Exception:
                            return compute()
                        if owner and self._singleflight_wait_timeout_seconds is None:
                            deadline = time.monotonic() + float(self._singleflight_ttl_seconds)
                    elif self._singleflight_wait_timeout_seconds is None:
                        deadline = time.monotonic() + float(self._singleflight_ttl_seconds)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"singleflight wait timed out for {stage}")
                    time.sleep(self._singleflight_poll_interval_seconds)
            renew = getattr(cache, "renew_stage_singleflight", None)
            if claimed and callable(renew):
                renew_stop = threading.Event()

                def _renew_loop() -> None:
                    while renew_stop is not None and not renew_stop.wait(self._singleflight_renew_interval_seconds):
                        try:
                            renewed = renew(
                                stage=stage,
                                fingerprint=fingerprint,
                                token=str(token or ""),
                                ttl_seconds=self._singleflight_ttl_seconds,
                            )
                        except Exception as exc:
                            renew_error.append(str(exc))
                            renew_stop.set()
                            return
                        if renewed:
                            continue
                        renew_error.append(str(getattr(cache, "last_error", "") or f"singleflight renew failed for {stage}").strip())
                        renew_stop.set()
                        return

                renew_thread = threading.Thread(
                    target=_renew_loop,
                    name=f"patent-stage-singleflight-renew-{stage}",
                    daemon=True,
                )
                renew_thread.start()
            result = compute()
            if renew_stop is not None:
                renew_stop.set()
            if renew_thread is not None and renew_thread.is_alive():
                renew_thread.join(timeout=0.05)
                if renew_thread.is_alive() and not renew_error:
                    renew_error.append(f"singleflight renew completion pending for {stage}")
            if not renew_error:
                try:
                    cache.set_stage_cache(
                        stage=stage,
                        fingerprint=fingerprint,
                        payload=dict(result or {}),
                        ttl_seconds=self._stage_cache_ttl_seconds,
                    )
                except Exception:
                    pass
            return result
        finally:
            if renew_stop is not None:
                renew_stop.set()
            if renew_thread is not None and renew_thread.is_alive():
                renew_thread.join(timeout=0.05)
            if claimed:
                try:
                    cache.clear_stage_singleflight(stage=stage, fingerprint=fingerprint, token=str(token or ""))
                except Exception:
                    pass

    def run(
        self,
        *,
        question: str,
        runtime: Any,
        conversation_context: dict[str, Any] | None = None,
        trace_id: str = "",
        progress_callback=None,
        content_callback=None,
    ) -> PatentQaExecutionResult:
        timings: dict[str, float] = {}
        normalized_trace_id = str(trace_id or "").strip()
        current_progress_step = ""
        retrieval_service = getattr(runtime, "retrieval_service", None)
        runtime_retrieval_signature = {
            "runtime_type": type(runtime).__name__,
            "retrieval_version": getattr(retrieval_service, "retrieval_version", ""),
            "catalog_index_version": getattr(retrieval_service, "catalog_index_version", ""),
            "stage2_query_model": getattr(runtime, "planning_model", ""),
        }
        _LOGGER.info(
            "patent pipeline start trace=%s question_chars=%s conversation_turns=%s runtime=%s planning_model=%s question=%s",
            normalized_trace_id,
            len(str(question or "")),
            len(list((conversation_context or {}).get("recent_turns_for_llm") or [])),
            type(runtime).__name__,
            getattr(runtime, "planning_model", ""),
            _question_preview(question),
        )
        try:
            _emit_progress_step(
                progress_callback,
                step="stage1",
                title="阶段一",
                message="阶段一：生成深度预回答与检索规划...",
            )
            current_progress_step = "stage1"
            stage1_fingerprint = build_stage1_cache_fingerprint(
                question=question,
                conversation_context=conversation_context,
                runtime_signature={
                    "planning_model": getattr(runtime, "planning_model", ""),
                    "stage1_prompt": getattr(runtime, "stage1_prompt", ""),
                },
            )
            stage1_result = self._timed(
                timings,
                "stage1",
                lambda: self._run_cached_stage(
                    stage="stage1",
                    fingerprint=stage1_fingerprint,
                    compute=lambda: runtime.stage1_pre_answer_and_planning(question, conversation_context=conversation_context),
                ),
            )
            retrieval_plan = _as_retrieval_plan(dict(stage1_result or {}).get("retrieval_plan"))
            retrieval_claims = _as_retrieval_claims(dict(stage1_result or {}).get("retrieval_claims"))
            deep_answer = str(dict(stage1_result or {}).get("deep_answer") or "")
            _LOGGER.info(
                "patent stage1 completed trace=%s success=%s retrieval_claims=%s deep_answer_chars=%s question_type=%s explicit_patent_ids=%s fallback=%s timing_ms=%s",
                normalized_trace_id,
                dict(stage1_result or {}).get("success", True),
                len(retrieval_claims),
                len(deep_answer),
                retrieval_plan.question_type,
                len(list(retrieval_plan.explicit_patent_ids or [])),
                dict(stage1_result or {}).get("fallback"),
                timings.get("stage1"),
            )
            if not retrieval_claims:
                success = bool(deep_answer.strip())
                steps = _build_stage_steps(
                    timings=timings,
                    stage25_result={},
                    success=success,
                )
                _LOGGER.info(
                    "patent pipeline short-circuit trace=%s success=%s reason=stage1_no_retrieval_claims deep_answer_chars=%s timings=%s",
                    normalized_trace_id,
                    success,
                    len(deep_answer),
                    timings,
                )
                return PatentQaExecutionResult(
                    success=success,
                    final_answer=deep_answer,
                    metadata=PatentQaExecutionMetadata(
                        route="kb_qa",
                        query_mode="patent staged qa",
                        source_ids=[],
                        stage_timings_ms=timings,
                        stage1_short_circuit=True,
                    ),
                    raw={
                        "stage1": stage1_result,
                        "references": [],
                        "reference_objects": [],
                        "reference_links": [],
                        "original_links": [],
                        "metadata": {"stage1_short_circuit": True},
                        "steps": steps,
                    },
                )
            _emit_progress_step(
                progress_callback,
                step="stage2",
                title="阶段二",
                message="阶段二：检索高相关专利摘要与片段...",
            )
            current_progress_step = "stage2"
            stage2_fingerprint = build_stage2_cache_fingerprint(
                question=question,
                retrieval_claims=retrieval_claims,
                retrieval_plan=retrieval_plan,
                runtime_signature=runtime_retrieval_signature,
            )
            stage2_result = self._timed(
                timings,
                "stage2",
                lambda: self._run_cached_stage(
                    stage="stage2",
                    fingerprint=stage2_fingerprint,
                    compute=lambda: runtime.stage2_targeted_retrieval(
                        retrieval_claims,
                        user_question=question,
                        should_cancel=None,
                        active_stream_count=None,
                    ),
                ),
            )
            _LOGGER.info(
                "patent stage2 completed trace=%s success=%s document_count=%s metadata_count=%s reference_count=%s timing_ms=%s",
                normalized_trace_id,
                dict(stage2_result or {}).get("success", True),
                _count_list_items(dict(stage2_result or {}).get("documents")),
                _count_list_items(dict(stage2_result or {}).get("metadatas")),
                _count_list_items(dict(stage2_result or {}).get("references")),
                timings.get("stage2"),
            )
            source_ids = list(runtime._extract_patent_ids_from_results(stage2_result) or [])
            _LOGGER.info(
                "patent stage2 extracted source_ids trace=%s count=%s sample=%s",
                normalized_trace_id,
                len(source_ids),
                source_ids[:10],
            )
            _emit_progress_step(
                progress_callback,
                step="stage25",
                title="阶段二点五",
                message="阶段二点五：尝试MD原文扩展检索...",
                data={"count": len(source_ids)},
            )
            current_progress_step = "stage25"
            stage25_fingerprint = build_stage25_cache_fingerprint(
                question=question,
                retrieval_results=stage2_result,
                source_ids=source_ids,
                skipped=bool(getattr(runtime, "stage25_is_noop", False)),
                skip_reason=str(getattr(runtime, "stage25_skip_reason", "") or "").strip(),
                runtime_signature=runtime_retrieval_signature,
            )
            stage25_result = self._timed(
                timings,
                "stage25",
                lambda: self._run_cached_stage(
                    stage="stage25",
                    fingerprint=stage25_fingerprint,
                    compute=lambda: runtime.stage25_patent_evidence_expansion(
                        retrieval_results=stage2_result,
                        user_question=question,
                        source_ids=source_ids,
                    ),
                ),
            )
            stage25_payload = dict(stage25_result or {})
            _LOGGER.info(
                "patent stage25 completed trace=%s skipped=%s skip_reason=%s source_id_count=%s timing_ms=%s",
                normalized_trace_id,
                bool(stage25_payload.get("skipped")),
                str(stage25_payload.get("skip_reason") or ""),
                len(source_ids),
                timings.get("stage25"),
            )
            stage3_input = stage25_payload.get("retrieval_results", stage2_result)
            stage_source_ids = list(stage25_payload.get("source_ids") or source_ids)
            _emit_progress_step(
                progress_callback,
                step="stage3",
                title="阶段三",
                message=f"阶段三：组装 {len(stage_source_ids)} 个专利的证据片段与表格...",
                data={"count": len(stage_source_ids)},
            )
            current_progress_step = "stage3"
            stage3_fingerprint = build_stage3_cache_fingerprint(
                retrieval_results=stage3_input,
                source_ids=stage_source_ids,
                force_pdf=bool(getattr(runtime, "stage3_force_pdf", False)),
                runtime_signature=runtime_retrieval_signature,
            )
            stage3_result = self._timed(
                timings,
                "stage3",
                lambda: self._run_cached_stage(
                    stage="stage3",
                    fingerprint=stage3_fingerprint,
                    compute=lambda: runtime.stage3_load_patent_evidence(
                        retrieval_results=stage3_input,
                        source_ids=stage_source_ids,
                        should_cancel=None,
                    ),
                ),
            )
            evidence_source_count, evidence_chunk_count = _evidence_counts(dict(stage3_result or {}))
            _LOGGER.info(
                "patent stage3 completed trace=%s source_id_count=%s evidence_source_count=%s evidence_chunk_count=%s force_pdf=%s timing_ms=%s",
                normalized_trace_id,
                len(stage_source_ids),
                evidence_source_count,
                evidence_chunk_count,
                bool(getattr(runtime, "stage3_force_pdf", False)),
                timings.get("stage3"),
            )
            _emit_progress_step(
                progress_callback,
                step="stage4",
                title="阶段四",
                message="阶段四：综合预回答与证据生成答案...",
                data={"count": len(stage_source_ids)},
            )
            current_progress_step = "stage4"
            _LOGGER.info(
                "patent stage4 starting trace=%s source_id_count=%s evidence_source_count=%s evidence_chunk_count=%s",
                normalized_trace_id,
                len(stage_source_ids),
                evidence_source_count,
                evidence_chunk_count,
            )
            stage4_fn = runtime.stage4_synthesis_with_patent_evidence
            stage4_kwargs = {
                "user_question": question,
                "deep_answer": deep_answer,
                "patent_evidence_bundle": stage3_result,
                "retrieval_results": stage3_input,
                "should_cancel": None,
                "conversation_context": conversation_context,
            }
            if callable(content_callback) and _callable_accepts_keyword(stage4_fn, "content_callback"):
                stage4_kwargs["content_callback"] = content_callback
            stage4_result = self._timed(
                timings,
                "stage4",
                lambda: stage4_fn(**stage4_kwargs),
            )
            final_payload = dict(stage4_result or {})
            merged_metadata = dict(dict(stage3_input or {}).get("metadata") or {})
            merged_metadata.update(dict(final_payload.get("metadata") or {}))
            final_answer = str(final_payload.get("final_answer") or final_payload.get("answer_text") or "")
            success = bool(final_payload.get("success")) if "success" in final_payload else bool(final_answer)
            steps = _build_stage_steps(
                timings=timings,
                stage25_result=dict(stage25_result or {}),
                success=success,
            )
            _LOGGER.info(
                "patent pipeline completed trace=%s success=%s final_answer_chars=%s references=%s source_id_count=%s timings=%s",
                normalized_trace_id,
                success,
                len(final_answer),
                len(_coalesce_stage4_list(final_payload, dict(stage3_input or {}), "references")),
                len(stage_source_ids),
                timings,
            )
            return PatentQaExecutionResult(
                success=success,
                final_answer=final_answer,
                metadata=PatentQaExecutionMetadata(
                    route="kb_qa",
                    query_mode=str(final_payload.get("query_mode") or "patent staged qa"),
                    source_ids=stage_source_ids,
                    stage_timings_ms=timings,
                    stage25_skipped=bool(stage25_payload.get("skipped")),
                    stage25_skip_reason=str(stage25_payload.get("skip_reason") or ""),
                ),
                raw={
                    "stage1": stage1_result,
                    "stage2": stage2_result,
                    "stage25": stage25_result,
                    "stage3": stage3_result,
                    "stage4": final_payload,
                    "references": _coalesce_stage4_list(final_payload, dict(stage3_input or {}), "references"),
                    "reference_objects": _coalesce_stage4_list(final_payload, dict(stage3_input or {}), "reference_objects"),
                    "reference_links": _coalesce_stage4_list(final_payload, dict(stage3_input or {}), "reference_links"),
                    "original_links": _coalesce_stage4_list(final_payload, dict(stage3_input or {}), "original_links"),
                    "metadata": merged_metadata,
                    "steps": steps,
                },
            )
        except Exception as exc:
            failed_stage = current_progress_step or next((key for key in ("stage4", "stage3", "stage25", "stage2", "stage1") if key in timings), "stage1")
            failed_title = {
                "stage1": "阶段一",
                "stage2": "阶段二",
                "stage25": "阶段二点五",
                "stage3": "阶段三",
                "stage4": "阶段四",
            }.get(failed_stage, failed_stage)
            _emit_progress_step(
                progress_callback,
                step=failed_stage,
                title=failed_title,
                message=f"{failed_title}失败",
                status="error",
                error=str(exc),
            )
            _LOGGER.exception(
                "patent pipeline failed trace=%s question=%s timings=%s error=%s",
                normalized_trace_id,
                _question_preview(question),
                timings,
                exc,
            )
            raise
