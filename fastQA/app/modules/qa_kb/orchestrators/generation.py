from __future__ import annotations

import inspect
import time
from typing import Any, Callable, Iterator

from app.integrations.redis import RedisService
from app.modules.generation_pipeline.evidence_rerank import rerank_evidence_chunks
from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.stage1_planning import effective_query_focus_terms_for_stage2
from app.modules.generation_pipeline.stage2_evidence_merge import maybe_merge_stage2_retrieval_evidence
from app.modules.generation_pipeline.stage2_focus_policy import rerank_dois_for_focus_evidence
from app.modules.graph_kb.models import GraphRagPayload
from app.modules.qa_cache.metrics import increment_cache_metric
from app.modules.qa_cache.pipeline_cache_flags import resolve_qa_pipeline_cache_redis
from app.modules.qa_cache.singleflight import run_singleflight
from app.modules.qa_cache.stage1_cache import (
    build_stage1_lock_key,
    cache_stage1_result,
    get_cached_stage1_result,
)
from app.modules.qa_cache.stage2_cache import (
    build_stage2_lock_key,
    cache_stage2_result,
    get_cached_stage2_result,
)
from app.modules.qa_cache.stage25_cache import (
    build_stage25_lock_key,
    cache_stage25_result,
    get_cached_stage25_result,
)
from app.modules.qa_cache.stage3_cache import (
    build_stage3_lock_key,
    cache_stage3_result,
    get_cached_stage3_result,
)
from app.modules.qa_kb.comparison_intent import build_comparison_plan, build_retrieval_claims_from_comparison_plan
from app.modules.qa_kb.models import GenerationRuntime, QaKbExecutionMetadata, QaKbExecutionResult
from app.modules.qa_kb.stages.pdf_loading import Stage3PdfLoader
from app.modules.qa_kb.stages.planning import Stage1Planner
from app.modules.qa_kb.stages.retrieval import Stage25MdExpansion, Stage2Retriever
from app.modules.qa_kb.stages.synthesis import Stage4Synthesizer
from app.modules.qa_kb.streaming import iter_result_events
from app.utils.upstream_errors import UpstreamCallError, build_sse_error_event, coerce_upstream_error
from app.utils.user_errors import humanize_exception, user_message_for_code


def _consume_stage4_result(stage4_output: Any, logger: Any) -> dict[str, Any]:
    if isinstance(stage4_output, dict):
        return stage4_output
    if hasattr(stage4_output, "__iter__"):
        final_dict: dict[str, Any] | None = None
        for item in stage4_output:
            if isinstance(item, dict):
                final_dict = item
        if final_dict is not None:
            return final_dict
    logger.warning("stage4 output did not yield a final result dict")
    return {"success": False, "error": "stage4_output_invalid"}


def _payload_cancelled(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return bool(payload.get("cancelled") or metadata.get("cancelled"))


def _should_cancelled(should_cancel: Callable[[], bool] | None) -> bool:
    if not callable(should_cancel):
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _short_preview(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _stage3_diag_enabled() -> bool:
    return env_bool("QA_STAGE3_DIAGNOSTIC_LOG", True)


def _evidence_counts(pdf_chunks: dict[str, list[dict[str, Any]]] | None) -> tuple[int, int]:
    chunks_by_source = dict(pdf_chunks or {})
    return len(chunks_by_source), sum(len(chunks or []) for chunks in chunks_by_source.values())


_RERANK_FALLBACK_MESSAGES = {
    "provider_disabled": "重排序服务未启用，已按向量相似度排序继续",
    "request_failed": "重排序服务请求失败，已按向量相似度排序继续",
    "empty_rerank_result": "重排序未返回有效结果，已按向量相似度排序继续",
    "empty_rerank_output": "重排序未返回有效结果，已按向量相似度排序继续",
}


def _degradation_warning_step(*, step: str, message: str, detail: str = "", data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "step",
        "step": step,
        "title": "降级提示",
        "message": message,
        "status": "warning",
    }
    if detail:
        payload["detail"] = detail
    if data:
        payload["data"] = dict(data)
    return payload


def _build_fatal_error_event(
    *,
    exc_or_state: Any,
    trace_id: str = "",
    default_code: str = "UPSTREAM_ERROR",
    default_error: str = "upstream_error",
    default_stage: str = "",
) -> dict[str, Any]:
    upstream: UpstreamCallError | None = None
    if isinstance(exc_or_state, UpstreamCallError):
        upstream = exc_or_state
    elif isinstance(exc_or_state, dict):
        upstream = coerce_upstream_error(exc_or_state.get("upstream_error") or exc_or_state)
    else:
        upstream = coerce_upstream_error(exc_or_state)

    if upstream is not None:
        return build_sse_error_event(upstream, trace_id=trace_id)

    message = humanize_exception(exc_or_state, code=default_code, error=default_error)
    event = {
        "type": "error",
        "code": default_code,
        "error": default_error,
        "message": message,
        "retriable": True,
        "trace_id": str(trace_id or ""),
    }
    if default_stage:
        event["failure_stage"] = default_stage
    return event


def _rerank_fallback_message(reason: str, *, status_code: int | None = None) -> str:
    clean = str(reason or "").strip().lower()
    if not clean:
        return "重排序服务不可用，已按向量相似度排序继续"
    if clean.startswith("rerank_exception:"):
        return "重排序服务不可用，已按向量相似度排序继续"
    for key, message in _RERANK_FALLBACK_MESSAGES.items():
        if key in clean:
            if status_code is not None:
                return f"{message}（HTTP {int(status_code)}）"
            return message
    base = "重排序服务不可用，已按向量相似度排序继续"
    if status_code is not None:
        return f"{base}（HTTP {int(status_code)}）"
    return base


def _stage2_rerank_degradation_info(stage2_result: dict[str, Any]) -> dict[str, Any] | None:
    claim_map = stage2_result.get("claim_to_results") or {}
    if not isinstance(claim_map, dict):
        return None
    for claim_data in claim_map.values():
        if not isinstance(claim_data, dict):
            continue
        rerank = claim_data.get("rerank") or {}
        if isinstance(rerank, dict) and rerank.get("fallback"):
            reason = str(rerank.get("reason") or rerank.get("fallback_reason") or "")
            status_code = rerank.get("status_code")
            normalized_status_code = int(status_code) if status_code is not None else None
            return {
                "message": _rerank_fallback_message(reason, status_code=normalized_status_code),
                "reason": reason,
                "status_code": normalized_status_code,
            }
    return None


def _stage2_rerank_degradation_message(stage2_result: dict[str, Any]) -> str:
    info = _stage2_rerank_degradation_info(stage2_result)
    return str(info.get("message") or "") if info else ""


def _final_query_mode(*, provided: Any, skip_pdf: bool) -> str:
    value = str(provided or "").strip()
    if value:
        return value
    return "生成驱动检索（MD直读）" if skip_pdf else "生成驱动检索（PDF溯源）"


def _dedupe_preserve_order(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        item = " ".join(value.split()).strip()
        return [item] if item else []
    if isinstance(value, list):
        return [" ".join(str(item or "").split()).strip() for item in value if str(item or "").strip()]
    return []


def build_retrieval_claims_from_answer_plan(answer_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(answer_plan, dict) or not answer_plan:
        return []
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append_claim(text: str, *, keywords: list[str] | None = None) -> None:
        claim = " ".join(str(text or "").split()).strip()
        if not claim or claim in seen:
            return
        seen.add(claim)
        claims.append(
            {
                "claim": claim,
                "keywords": list(keywords or []),
                "preferred_sections": ["methods", "results", "discussion"],
                "filters": {},
                "source": "answer_plan",
            }
        )

    for item in list(answer_plan.get("dimensions") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        needs = _as_text_list(item.get("evidence_needed") or item.get("evidence_needs"))
        for need in needs:
            _append_claim(f"{name}：{need}" if name else need, keywords=[name] if name else [])

    for item in list(answer_plan.get("evidence_needs") or []):
        if isinstance(item, dict):
            topic = str(item.get("topic") or item.get("dimension") or "").strip()
            needs = _as_text_list(item.get("need") or item.get("evidence_needed") or item.get("description"))
            for need in needs:
                _append_claim(f"{topic}：{need}" if topic else need, keywords=[topic] if topic else [])
        else:
            for need in _as_text_list(item):
                _append_claim(need)

    for item in list(answer_plan.get("object_analysis_plan") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("object") or item.get("label") or "").strip()
        needs = _as_text_list(item.get("must_verify_with_evidence") or item.get("evidence_needed"))
        for need in needs:
            _append_claim(f"{label}：{need}" if label else need, keywords=[label] if label else [])

    return claims


def select_source_dois_for_evidence(
    *,
    retrieval_results: dict[str, Any],
    dois: list[str],
    user_question: str | None = None,
    query_focus_terms: list[str] | None = None,
) -> list[str]:
    """Keep the evidence expansion set small enough for Stage4 to stay grounded."""
    deduped_ordered = _dedupe_preserve_order(dois)
    if not deduped_ordered:
        return []

    workflow_dois, focus_audit = rerank_dois_for_focus_evidence(
        ordered_dois=deduped_ordered,
        retrieval_results=retrieval_results,
        user_question=str(user_question or ""),
        query_focus_terms=list(query_focus_terms or []),
    )
    if isinstance(retrieval_results, dict) and isinstance(focus_audit, dict):
        retrieval_results.setdefault("focus_policy_audit", focus_audit)

    ordered_dois = workflow_dois or deduped_ordered

    max_total = env_int("QA_SOURCE_DOI_MAX_TOTAL", 15, minimum=1, maximum=100)
    max_non_comparison = env_int("QA_SOURCE_DOI_MAX_TOTAL_NON_COMPARISON", 20, minimum=1, maximum=100)
    max_per_object = env_int("QA_SOURCE_DOI_MAX_PER_COMPARISON_OBJECT", 5, minimum=1, maximum=20)
    groups = list(retrieval_results.get("comparison_groups") or []) if isinstance(retrieval_results, dict) else []
    valid_groups = [group for group in groups if isinstance(group, dict) and group.get("doi_candidates")]
    if not valid_groups:
        return ordered_dois[:max_non_comparison]

    allowed_from_stage2 = set(ordered_dois)
    selected: list[str] = []
    seen: set[str] = set()
    grouped_dois = [
        [doi for doi in _dedupe_preserve_order(list(group.get("doi_candidates") or [])) if doi in allowed_from_stage2][:max_per_object]
        for group in valid_groups
    ]
    for index in range(max_per_object):
        for group_dois in grouped_dois:
            if index >= len(group_dois):
                continue
            doi = group_dois[index]
            if doi in seen:
                continue
            selected.append(doi)
            seen.add(doi)
            if len(selected) >= max_total:
                return selected

    # Fill any remaining slots with the original Stage2 rank order.
    for doi in ordered_dois:
        if doi in seen:
            continue
        selected.append(doi)
        seen.add(doi)
        if len(selected) >= max_total:
            break
    return selected


def apply_selected_dois_to_comparison_groups(*, retrieval_results: dict[str, Any], selected_dois: list[str]) -> None:
    if not isinstance(retrieval_results, dict):
        return
    selected = set(_dedupe_preserve_order(selected_dois))
    if not selected:
        return
    for group in list(retrieval_results.get("comparison_groups") or []):
        if not isinstance(group, dict):
            continue
        group["doi_candidates"] = [
            doi
            for doi in _dedupe_preserve_order(list(group.get("doi_candidates") or []))
            if doi in selected
        ]
def _model_identity_shortcut(question: str) -> str | None:
    qlow = str(question or "").lower()
    model_queries = (
        "什么模型",
        "是什么模型",
        "which model",
        "what model",
        "你是谁",
        "who are you",
        "who created",
        "是谁",
        "哪个模型",
    )
    if any(keyword in qlow for keyword in model_queries):
        return (
            "您好，我是运行在claude-4.5-sonnet-thinking模型上的AI助手，"
            "很高兴在Cursor IDE中为您提供帮助，你可以直接告诉我你的具体需求。"
        )
    return None


class GenerationPipelineOrchestrator:
    def __init__(
        self,
        *,
        stage1: Stage1Planner | None = None,
        stage2: Stage2Retriever | None = None,
        stage25: Stage25MdExpansion | None = None,
        stage3: Stage3PdfLoader | None = None,
        stage4: Stage4Synthesizer | None = None,
        evaluate_stage3_pdf_skip_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        merge_pdf_chunks_with_md_fn: Callable[..., dict[str, list[dict[str, Any]]]] | None = None,
        merge_stage2_retrieval_evidence_fn: Callable[..., dict[str, list[dict[str, Any]]]] | None = None,
        evidence_rerank_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.stage1 = stage1 or Stage1Planner()
        self.stage2 = stage2 or Stage2Retriever()
        self.stage25 = stage25 or Stage25MdExpansion()
        self.stage3 = stage3 or Stage3PdfLoader()
        self.stage4 = stage4 or Stage4Synthesizer()
        self.evaluate_stage3_pdf_skip_fn = evaluate_stage3_pdf_skip_fn or (lambda **_kwargs: {"should_skip": False, "reason": ""})
        self.merge_pdf_chunks_with_md_fn = merge_pdf_chunks_with_md_fn
        self.merge_stage2_retrieval_evidence_fn = merge_stage2_retrieval_evidence_fn or maybe_merge_stage2_retrieval_evidence
        self.evidence_rerank_fn = evidence_rerank_fn or rerank_evidence_chunks

    def _timed(self, timings: dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        result = fn()
        timings[key] = round((time.perf_counter() - started) * 1000, 3)
        return result

    @staticmethod
    def _intent_step_from_stage1(stage1_result: dict[str, Any]) -> dict[str, Any] | None:
        intent_result = stage1_result.get("intent_detect") if isinstance(stage1_result, dict) else None
        if not isinstance(intent_result, dict):
            return None
        intent_tag = str(intent_result.get("intent_tag") or "generic").strip() or "generic"
        ok = bool(intent_result.get("ok"))
        data: dict[str, Any] = {
            "intent_tag": intent_tag,
            "ok": ok,
        }
        elapsed_ms = intent_result.get("elapsed_ms")
        if isinstance(elapsed_ms, (int, float)) and elapsed_ms >= 0:
            data["elapsed_ms"] = round(float(elapsed_ms), 3)
        model = str(intent_result.get("model") or "").strip()
        if model:
            data["model"] = model
        error = str(intent_result.get("error") or "").strip()
        return {
            "type": "step",
            "step": "intent_detect",
            "title": "意图识别",
            "message": "意图识别失败，已按通用模式继续" if not ok else f"意图识别：{intent_tag}",
            "detail": "快速判断问题意图，辅助阶段一规划",
            "status": "success" if ok else "warning",
            "data": data,
            **({"error": error} if error and not ok else {}),
        }

    @staticmethod
    def _supports_kwarg(target: Callable[..., Any], name: str) -> bool:
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            return False
        parameters = signature.parameters
        return name in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())

    @staticmethod
    def _graph_cache_fingerprint(graph_evidence: GraphRagPayload | None) -> str:
        if graph_evidence is None:
            return "none"
        return str(graph_evidence.cache_fingerprint or "none").strip() or "none"

    @staticmethod
    def _dedupe_preserve_order(values: list[str] | tuple[str, ...]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for item in list(values or []):
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered

    def _fallback_result(
        self,
        *,
        final_answer: str,
        query_mode: str,
        timings: dict[str, float],
        raw: dict[str, Any],
    ) -> QaKbExecutionResult:
        return QaKbExecutionResult(
            success=True,
            final_answer=str(final_answer or ""),
            metadata=QaKbExecutionMetadata(
                route="kb_qa",
                pipeline_mode="new",
                query_mode=query_mode,
                use_generation_driven=True,
                doi_source=str((raw or {}).get("doi_source") or "none"),
                stage_timings_ms=timings,
            ),
            raw=raw,
        )

    def _enhance_comparison_plan_with_profile(
        self,
        *,
        runtime: GenerationRuntime,
        question: str,
        comparison_plan: dict[str, Any],
        retrieval_claims: list[dict[str, Any]],
        logger: Any,
    ) -> dict[str, Any]:
        if not comparison_plan.get("enabled"):
            return comparison_plan
        generator = getattr(runtime, "generate_comparison_retrieval_profile", None)
        if not callable(generator):
            return comparison_plan
        try:
            profiled = generator(
                user_question=question,
                comparison_plan=comparison_plan,
                retrieval_claims=retrieval_claims,
            )
        except Exception as exc:
            logger.warning("comparison retrieval profile generation failed: %s", exc)
            return comparison_plan
        if isinstance(profiled, dict) and profiled.get("enabled"):
            return profiled
        return comparison_plan

    def _run_stage1(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        redis_service: RedisService | None,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence: GraphRagPayload | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        redis_service = resolve_qa_pipeline_cache_redis(redis_service)
        cached = get_cached_stage1_result(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            conversation_context=conversation_context,
            graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
        )
        if cached is not None:
            increment_cache_metric("stage1", "cache_hit")
            return cached
        increment_cache_metric("stage1", "cache_miss")

        def _compute() -> dict[str, Any]:
            kwargs = {
                "runtime": runtime,
                "user_question": question,
                "conversation_context": conversation_context,
            }
            if self._supports_kwarg(self.stage1.run, "graph_evidence"):
                kwargs["graph_evidence"] = graph_evidence
            if self._supports_kwarg(self.stage1.run, "should_cancel"):
                kwargs["should_cancel"] = should_cancel
            result = self.stage1.run(**kwargs)
            if not _payload_cancelled(result) and not _should_cancelled(should_cancel):
                cache_stage1_result(
                    redis_service=redis_service,
                    runtime=runtime,
                    question=question,
                    stage1_result=result,
                    conversation_context=conversation_context,
                    graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
                )
            return result

        if redis_service is None or not redis_service.available:
            return _compute()

        return run_singleflight(
            redis_service=redis_service,
            lock_key=build_stage1_lock_key(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                conversation_context=conversation_context,
                graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
            ),
            namespace="stage1",
            read_cached_fn=lambda: get_cached_stage1_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                conversation_context=conversation_context,
                graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
            ),
            compute_fn=_compute,
        )

    def _run_stage2(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        retrieval_claims: list[dict[str, Any]],
        redis_service: RedisService | None,
        n_results_per_claim: int,
        should_cancel: Callable[[], bool] | None,
        active_stream_count: int | None,
        graph_evidence: GraphRagPayload | None,
        comparison_plan: dict[str, Any] | None = None,
        query_focus_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        redis_service = resolve_qa_pipeline_cache_redis(redis_service)
        cached = get_cached_stage2_result(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
            graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
            query_focus_terms=query_focus_terms,
        )
        if cached is not None:
            increment_cache_metric("stage2", "cache_hit")
            return cached
        increment_cache_metric("stage2", "cache_miss")

        def _compute() -> dict[str, Any]:
            kwargs = {
                "runtime": runtime,
                "retrieval_claims": retrieval_claims,
                "n_results_per_claim": n_results_per_claim,
                "user_question": question,
                "should_cancel": should_cancel,
                "active_stream_count": active_stream_count,
            }
            if self._supports_kwarg(self.stage2.run, "graph_evidence"):
                kwargs["graph_evidence"] = graph_evidence
            if self._supports_kwarg(self.stage2.run, "comparison_plan"):
                kwargs["comparison_plan"] = comparison_plan
            if self._supports_kwarg(self.stage2.run, "query_focus_terms"):
                kwargs["query_focus_terms"] = query_focus_terms
            result = self.stage2.run(**kwargs)
            if not _payload_cancelled(result) and not _should_cancelled(should_cancel):
                cache_stage2_result(
                    redis_service=redis_service,
                    runtime=runtime,
                    question=question,
                    retrieval_claims=retrieval_claims,
                    n_results_per_claim=n_results_per_claim,
                    stage2_result=result,
                    graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
                    query_focus_terms=query_focus_terms,
                )
            return result

        if redis_service is None or not redis_service.available:
            return _compute()

        return run_singleflight(
            redis_service=redis_service,
            lock_key=build_stage2_lock_key(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_claims=retrieval_claims,
                n_results_per_claim=n_results_per_claim,
                graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
                query_focus_terms=query_focus_terms,
            ),
            namespace="stage2",
            read_cached_fn=lambda: get_cached_stage2_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_claims=retrieval_claims,
                n_results_per_claim=n_results_per_claim,
                graph_cache_fingerprint=self._graph_cache_fingerprint(graph_evidence),
                query_focus_terms=query_focus_terms,
            ),
            compute_fn=_compute,
        )

    def _run_stage25(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        retrieval_results: dict[str, Any],
        dois: list[str],
        redis_service: RedisService | None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        redis_service = resolve_qa_pipeline_cache_redis(redis_service)
        cached = get_cached_stage25_result(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_results=retrieval_results,
            dois=dois,
        )
        if cached is not None:
            increment_cache_metric("stage25", "cache_hit")
            return cached
        increment_cache_metric("stage25", "cache_miss")

        def _compute() -> dict[str, Any]:
            result = self.stage25.run(runtime=runtime, retrieval_results=retrieval_results, user_question=question, dois=dois)
            if not _payload_cancelled(result) and not _should_cancelled(should_cancel):
                cache_stage25_result(
                    redis_service=redis_service,
                    runtime=runtime,
                    question=question,
                    retrieval_results=retrieval_results,
                    dois=dois,
                    stage25_result=result,
                )
            return result

        if redis_service is None or not redis_service.available:
            return _compute()

        return run_singleflight(
            redis_service=redis_service,
            lock_key=build_stage25_lock_key(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_results=retrieval_results,
                dois=dois,
            ),
            namespace="stage25",
            read_cached_fn=lambda: get_cached_stage25_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_results=retrieval_results,
                dois=dois,
            ),
            compute_fn=_compute,
        )

    def _merge_stage2_into_evidence(
        self,
        *,
        pdf_chunks: dict[str, list[dict[str, Any]]],
        retrieval_results: dict[str, Any],
        dois: list[str],
        logger: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        merge_fn = self.merge_stage2_retrieval_evidence_fn
        if merge_fn is None:
            return dict(pdf_chunks or {})
        return merge_fn(
            retrieval_results=retrieval_results,
            dois_ordered=list(dois or []),
            pdf_chunks=dict(pdf_chunks or {}),
            logger=logger,
        )

    def _run_stage35_evidence_rerank(
        self,
        *,
        runtime: GenerationRuntime,
        question: str,
        retrieval_results: dict[str, Any],
        pdf_chunks: dict[str, list[dict[str, Any]]],
        logger: Any,
    ) -> dict[str, Any]:
        if not env_bool("QA_STAGE35_EVIDENCE_RERANK_ENABLED", True):
            chunk_count = sum(len(chunks) for chunks in (pdf_chunks or {}).values())
            return {
                "pdf_chunks": pdf_chunks,
                "stats": {"enabled": False, "before_chunk_count": chunk_count, "after_chunk_count": chunk_count},
            }
        embedding_model = None
        try:
            literature_expert = getattr(runtime, "literature_expert", None)
            embedding_model = getattr(literature_expert, "embedding_model", None) if literature_expert is not None else None
        except Exception:
            embedding_model = None
        try:
            result = self.evidence_rerank_fn(
                pdf_chunks=pdf_chunks,
                user_question=question,
                retrieval_results=retrieval_results,
                embedding_model=embedding_model,
            )
        except Exception as exc:
            logger.warning("stage35 evidence rerank failed, using unranked chunks: %s", exc)
            chunk_count = sum(len(chunks) for chunks in (pdf_chunks or {}).values())
            return {
                "pdf_chunks": pdf_chunks,
                "stats": {
                    "enabled": True,
                    "failed": True,
                    "before_chunk_count": chunk_count,
                    "after_chunk_count": chunk_count,
                    "error": str(exc),
                },
            }
        if not isinstance(result, dict) or not isinstance(result.get("pdf_chunks"), dict):
            return {"pdf_chunks": pdf_chunks, "stats": {"enabled": True, "invalid_result": True}}
        logger.info("stage35 evidence rerank completed stats=%s", dict(result.get("stats") or {}))
        return result

    def _run_stage3(
        self,
        *,
        runtime: GenerationRuntime,
        dois: list[str],
        redis_service: RedisService | None,
        max_chunks_per_doi: int,
        should_cancel: Callable[[], bool] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        redis_service = resolve_qa_pipeline_cache_redis(redis_service)
        cached = get_cached_stage3_result(
            redis_service=redis_service,
            dois=dois,
            max_chunks_per_doi=max_chunks_per_doi,
        )
        if cached is not None:
            increment_cache_metric("stage3", "cache_hit")
            return cached
        increment_cache_metric("stage3", "cache_miss")

        def _compute() -> dict[str, list[dict[str, Any]]]:
            result = self.stage3.run(runtime=runtime, dois=dois, max_chunks_per_doi=max_chunks_per_doi, should_cancel=should_cancel)
            if not _payload_cancelled(result) and not _should_cancelled(should_cancel):
                cache_stage3_result(
                    redis_service=redis_service,
                    dois=dois,
                    max_chunks_per_doi=max_chunks_per_doi,
                    stage3_result=result,
                )
            return result

        if redis_service is None or not redis_service.available:
            return _compute()

        return run_singleflight(
            redis_service=redis_service,
            lock_key=build_stage3_lock_key(
                redis_service=redis_service,
                dois=dois,
                max_chunks_per_doi=max_chunks_per_doi,
            ),
            namespace="stage3",
            read_cached_fn=lambda: get_cached_stage3_result(
                redis_service=redis_service,
                dois=dois,
                max_chunks_per_doi=max_chunks_per_doi,
            ),
            compute_fn=_compute,
        )

    def _prepare(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        redis_service: RedisService | None,
        n_results_per_claim: int,
        should_cancel: Callable[[], bool] | None,
        active_stream_count: int | None,
        logger: Any,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence: GraphRagPayload | None = None,
    ) -> QaKbExecutionResult | dict[str, Any]:
        timings: dict[str, float] = {}
        logger.info(
            "fastqa stream pipeline start question_chars=%s n_results_per_claim=%s active_stream_count=%s",
            len(str(question or "")),
            n_results_per_claim,
            active_stream_count,
        )
        model_identity_answer = _model_identity_shortcut(question)
        if model_identity_answer:
            return QaKbExecutionResult(
                success=True,
                final_answer=model_identity_answer,
                metadata=QaKbExecutionMetadata(
                    route="kb_qa",
                    pipeline_mode="new",
                    query_mode="model_identity_shortcut",
                    use_generation_driven=True,
                    stage_timings_ms=timings,
                ),
                raw={"shortcut": "model_identity"},
            )

        stage1_result = self._timed(
            timings,
            "stage1",
            lambda: self._run_stage1(
                question=question,
                runtime=runtime,
                redis_service=redis_service,
                conversation_context=conversation_context,
                graph_evidence=graph_evidence,
                should_cancel=should_cancel,
            ),
        )
        if not stage1_result.get("success"):
            return QaKbExecutionResult(
                success=False,
                final_answer="",
                metadata=QaKbExecutionMetadata(
                    route="kb_qa",
                    pipeline_mode="new",
                    query_mode="生成驱动检索（阶段一失败）",
                    use_generation_driven=True,
                    stage_timings_ms=timings,
                ),
                raw={"error": stage1_result.get("error"), "stage1_result": stage1_result},
            )

        deep_answer = str(stage1_result.get("deep_answer") or "")
        answer_plan = stage1_result.get("answer_plan") if isinstance(stage1_result.get("answer_plan"), dict) else {}
        retrieval_claims = list(stage1_result.get("retrieval_claims") or [])
        retrieval_claims.extend(build_retrieval_claims_from_answer_plan(answer_plan))
        comparison_plan = build_comparison_plan(
            question,
            stage1_result=stage1_result,
            retrieval_claims=[item for item in retrieval_claims if isinstance(item, dict)],
        )
        comparison_plan = self._enhance_comparison_plan_with_profile(
            runtime=runtime,
            question=question,
            comparison_plan=comparison_plan,
            retrieval_claims=[item for item in retrieval_claims if isinstance(item, dict)],
            logger=logger,
        )
        if comparison_plan.get("enabled"):
            retrieval_claims = build_retrieval_claims_from_comparison_plan(comparison_plan) + build_retrieval_claims_from_answer_plan(answer_plan)
        stage1_query_focus_terms = effective_query_focus_terms_for_stage2(stage1_result)
        logger.info(
            "fastqa stage1 normalized deep_answer_chars=%s retrieval_claims=%s answer_plan_keys=%s "
            "comparison_enabled=%s query_focus_terms=%s question=%s",
            len(deep_answer),
            len(retrieval_claims),
            sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
            bool(comparison_plan.get("enabled")),
            stage1_query_focus_terms,
            _short_preview(question),
        )
        if not retrieval_claims:
            logger.warning(
                "fastqa pipeline short-circuit reason=stage1_no_retrieval_claims deep_answer_chars=%s "
                "answer_plan_keys=%s comparison_enabled=%s fallback=%s question=%s",
                len(deep_answer),
                sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
                bool(comparison_plan.get("enabled")),
                str(stage1_result.get("fallback") or ""),
                _short_preview(question),
            )
            return self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（仅预回答）",
                timings=timings,
                raw={
                    "deep_answer": deep_answer,
                    "retrieval_claims": retrieval_claims,
                    "stage1_result": stage1_result,
                    "answer_plan": answer_plan,
                    "comparison_plan": comparison_plan,
                },
            )

        logger.info(
            "fastqa entering stage2.run retrieval_claims=%s query_focus_terms=%s comparison_enabled=%s question=%s",
            len(retrieval_claims),
            stage1_query_focus_terms,
            bool(comparison_plan.get("enabled")),
            _short_preview(question),
        )
        stage2_result = self._timed(
            timings,
            "stage2",
            lambda: self._run_stage2(
                question=question,
                runtime=runtime,
                retrieval_claims=retrieval_claims,
                redis_service=redis_service,
                n_results_per_claim=n_results_per_claim,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
                graph_evidence=graph_evidence,
                comparison_plan=comparison_plan,
                query_focus_terms=stage1_query_focus_terms,
            ),
        )
        logger.info(
            "fastqa stage2 returned success=%s unique_count=%s total_count=%s claim_groups=%s question=%s",
            stage2_result.get("success"),
            stage2_result.get("unique_count"),
            stage2_result.get("total_count"),
            len(dict(stage2_result.get("claim_to_results") or {})),
            _short_preview(question),
        )
        if not stage2_result.get("success"):
            logger.warning(
                "fastqa pipeline fallback reason=stage2_failed error=%s cancelled=%s retrieval_claims=%s question=%s",
                str(stage2_result.get("error") or ""),
                bool(stage2_result.get("cancelled")),
                len(retrieval_claims),
                _short_preview(question),
            )
            return QaKbExecutionResult(
                success=False,
                final_answer="",
                metadata=QaKbExecutionMetadata(
                    route="kb_qa",
                    pipeline_mode="new",
                    query_mode="生成驱动检索（检索失败）",
                    use_generation_driven=True,
                    stage_timings_ms=timings,
                ),
                raw={
                    "error": stage2_result.get("error"),
                    "upstream_error": stage2_result.get("upstream_error"),
                    "retrieval_results": stage2_result,
                    "deep_answer": deep_answer,
                    "retrieval_claims": retrieval_claims,
                    "comparison_plan": comparison_plan,
                },
            )

        dois = list(runtime._extract_dois_from_results(stage2_result))
        all_stage2_dois = _dedupe_preserve_order(dois)
        doi_source = "retrieval" if dois else "none"
        logger.info(
            "fastqa stream stage2 extracted doi_count=%s doi_sample=%s",
            len(dois),
            dois[:10],
        )
        if not dois and graph_evidence is not None and graph_evidence.stage2_doi_candidates:
            dois = self._dedupe_preserve_order(graph_evidence.stage2_doi_candidates)[: max(1, int(n_results_per_claim))]
            all_stage2_dois = list(dois)
            doi_source = "graph_seeded" if dois else "none"
            logger.info("fastqa stream graph-seeded doi fallback engaged doi_count=%s doi_sample=%s", len(dois), dois[:10])
        if dois:
            selected_dois = select_source_dois_for_evidence(
                retrieval_results=stage2_result,
                dois=dois,
                user_question=question,
                query_focus_terms=stage1_query_focus_terms,
            )
            if selected_dois != _dedupe_preserve_order(dois):
                logger.info(
                    "fastqa stream source doi gate reduced doi_count=%s->%s doi_sample=%s",
                    len(_dedupe_preserve_order(dois)),
                    len(selected_dois),
                    selected_dois[:10],
                )
            dois = selected_dois
            apply_selected_dois_to_comparison_groups(retrieval_results=stage2_result, selected_dois=dois)
        if not dois:
            logger.warning(
                "fastqa pipeline fallback reason=stage2_no_doi unique_count=%s total_count=%s "
                "all_stage2_dois=%s doi_source=%s query_focus_terms=%s question=%s",
                stage2_result.get("unique_count"),
                stage2_result.get("total_count"),
                len(all_stage2_dois),
                doi_source,
                stage1_query_focus_terms,
                _short_preview(question),
            )
            return self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（无DOI，仅预回答）",
                timings=timings,
                raw={
                    "deep_answer": deep_answer,
                    "retrieval_claims": retrieval_claims,
                    "retrieval_results": stage2_result,
                    "dois": [],
                    "all_stage2_dois": all_stage2_dois,
                    "doi_source": doi_source,
                    "comparison_plan": comparison_plan,
                },
            )

        if _stage3_diag_enabled():
            logger.info(
                "fastqa stage3 handoff doi_count=%s all_stage2_dois=%s doi_source=%s doi_sample=%s "
                "stage2_unique_count=%s stage2_total_count=%s query_focus_terms=%s",
                len(dois),
                len(all_stage2_dois),
                doi_source,
                list(dois or [])[:10],
                stage2_result.get("unique_count"),
                stage2_result.get("total_count"),
                stage1_query_focus_terms,
            )

        md_expansion_result = {
            "enabled": False,
            "applied": False,
            "md_chunks_by_doi": {},
            "stats": {"hit_doi_count": 0, "total_md_chunks": 0, "fallback_reason": ""},
        }
        try:
            md_expansion_result = self._timed(
                timings,
                "stage25",
                lambda: self._run_stage25(
                    question=question,
                    runtime=runtime,
                    retrieval_results=stage2_result,
                    dois=dois,
                    redis_service=redis_service,
                    should_cancel=should_cancel,
                ),
            )
        except Exception as exc:
            logger.warning("stage25 md expansion failed, falling back to PDF path: %s", exc)

        skip_decision = self.evaluate_stage3_pdf_skip_fn(md_expansion_result=md_expansion_result)
        skip_pdf = bool(skip_decision.get("should_skip"))
        skip_reason = str(skip_decision.get("reason") or "")
        if _stage3_diag_enabled():
            logger.info(
                "fastqa stage3 skip decision skip_pdf=%s skip_reason=%s decision=%s md_applied=%s md_stats=%s",
                skip_pdf,
                skip_reason,
                dict(skip_decision or {}),
                bool(md_expansion_result.get("applied")),
                dict(md_expansion_result.get("stats") or {}),
            )

        if skip_pdf:
            pdf_chunks = dict(md_expansion_result.get("md_chunks_by_doi") or {})
            timings["stage3"] = 0.0
        else:
            pdf_chunks = self._timed(
                timings,
                "stage3",
                lambda: self._run_stage3(
                    runtime=runtime,
                    dois=dois,
                    redis_service=redis_service,
                    max_chunks_per_doi=3,
                    should_cancel=should_cancel,
                ),
            )
            if md_expansion_result.get("applied") and self.merge_pdf_chunks_with_md_fn is not None and md_expansion_result.get("md_chunks_by_doi"):
                pdf_chunks = self.merge_pdf_chunks_with_md_fn(
                    pdf_chunks=pdf_chunks,
                    md_chunks=md_expansion_result.get("md_chunks_by_doi", {}),
                )

        if _stage3_diag_enabled():
            raw_source_count, raw_chunk_count = _evidence_counts(pdf_chunks)
            logger.info(
                "fastqa stage3 raw completed skip_pdf=%s skip_reason=%s source_count=%s chunk_count=%s "
                "stage25_applied=%s",
                skip_pdf,
                skip_reason,
                raw_source_count,
                raw_chunk_count,
                bool(md_expansion_result.get("applied")),
            )
        merge_before_source_count, merge_before_chunk_count = _evidence_counts(pdf_chunks)
        pdf_chunks = self._merge_stage2_into_evidence(
            pdf_chunks=pdf_chunks,
            retrieval_results=stage2_result,
            dois=dois,
            logger=logger,
        )
        if _stage3_diag_enabled():
            merge_after_source_count, merge_after_chunk_count = _evidence_counts(pdf_chunks)
            logger.info(
                "fastqa stage3 evidence merge completed before_sources=%s before_chunks=%s "
                "after_sources=%s after_chunks=%s",
                merge_before_source_count,
                merge_before_chunk_count,
                merge_after_source_count,
                merge_after_chunk_count,
            )

        evidence_rerank_result = self._timed(
            timings,
            "stage35",
            lambda: self._run_stage35_evidence_rerank(
                runtime=runtime,
                question=question,
                retrieval_results=stage2_result,
                pdf_chunks=pdf_chunks,
                logger=logger,
            ),
        )
        pdf_chunks = dict(evidence_rerank_result.get("pdf_chunks") or pdf_chunks)
        if _stage3_diag_enabled():
            rerank_source_count, rerank_chunk_count = _evidence_counts(pdf_chunks)
            logger.info(
                "fastqa stage35 completed stats=%s source_count=%s chunk_count=%s",
                dict(evidence_rerank_result.get("stats") or {}),
                rerank_source_count,
                rerank_chunk_count,
            )

        return {
            "timings": timings,
            "deep_answer": deep_answer,
            "answer_plan": answer_plan,
            "retrieval_claims": retrieval_claims,
            "retrieval_results": stage2_result,
            "dois": dois,
            "all_stage2_dois": all_stage2_dois,
            "doi_source": doi_source,
            "pdf_chunks": pdf_chunks,
            "evidence_rerank": evidence_rerank_result,
            "md_expansion": md_expansion_result,
            "comparison_plan": comparison_plan,
            "skip_pdf": skip_pdf,
            "skip_reason": skip_reason,
        }

    def run(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        redis_service: RedisService | None = None,
        n_results_per_claim: int,
        should_cancel: Callable[[], bool] | None,
        active_stream_count: int | None,
        logger: Any,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence: GraphRagPayload | None = None,
    ) -> QaKbExecutionResult:
        prepared = self._prepare(
            question=question,
            runtime=runtime,
            redis_service=redis_service,
            n_results_per_claim=n_results_per_claim,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            logger=logger,
            conversation_context=conversation_context,
            graph_evidence=graph_evidence,
        )
        if isinstance(prepared, QaKbExecutionResult):
            return prepared

        stage4_output = self._timed(
            prepared["timings"],
            "stage4",
            lambda: self.stage4.stream(
                **(
                    {
                        "runtime": runtime,
                        "user_question": question,
                        "deep_answer": prepared["deep_answer"],
                        "pdf_chunks": prepared["pdf_chunks"],
                        "retrieval_results": prepared["retrieval_results"],
                        "should_cancel": should_cancel,
                        "conversation_context": conversation_context,
                        **(
                            {"answer_plan": prepared.get("answer_plan")}
                            if self._supports_kwarg(self.stage4.stream, "answer_plan")
                            else {}
                        ),
                        **(
                            {"graph_evidence": graph_evidence}
                            if self._supports_kwarg(self.stage4.stream, "graph_evidence")
                            else {}
                        ),
                    }
                )
            ),
        )
        synthesis_result = _consume_stage4_result(stage4_output, logger)
        if not synthesis_result.get("success"):
            return QaKbExecutionResult(
                success=False,
                final_answer="",
                metadata=QaKbExecutionMetadata(
                    route="kb_qa",
                    pipeline_mode="new",
                    query_mode="生成驱动检索（合成失败）",
                    use_generation_driven=True,
                    stage_timings_ms=prepared["timings"],
                ),
                raw={
                    **prepared,
                    "synthesis_result": synthesis_result,
                    "error": synthesis_result.get("error"),
                    "upstream_error": synthesis_result.get("upstream_error"),
                },
            )

        return QaKbExecutionResult(
            success=True,
            final_answer=str(synthesis_result.get("final_answer") or ""),
            metadata=QaKbExecutionMetadata(
                route="kb_qa",
                pipeline_mode="new",
                query_mode=_final_query_mode(provided=synthesis_result.get("query_mode"), skip_pdf=prepared["skip_pdf"]),
                use_generation_driven=True,
                doi_source=str(prepared.get("doi_source") or "none"),
                doi_count=len(prepared["dois"]),
                chunk_count=sum(len(chunks) for chunks in prepared["pdf_chunks"].values()),
                source_count=len(prepared["pdf_chunks"]),
                stage3_pdf_skipped=prepared["skip_pdf"],
                stage3_pdf_skip_reason=prepared["skip_reason"],
                stage_timings_ms=prepared["timings"],
            ),
            raw={**prepared, "synthesis_result": synthesis_result},
        )

    def stream(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        redis_service: RedisService | None = None,
        n_results_per_claim: int,
        should_cancel: Callable[[], bool] | None,
        active_stream_count: int | None,
        logger: Any,
        sse_event: Callable[[dict[str, Any]], Any],
        chunk_size: int = 120,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence: GraphRagPayload | None = None,
    ) -> Iterator[Any]:
        timings: dict[str, float] = {}
        logger.info(
            "fastqa stream pipeline start question_chars=%s n_results_per_claim=%s active_stream_count=%s",
            len(str(question or "")),
            n_results_per_claim,
            active_stream_count,
        )
        model_identity_answer = _model_identity_shortcut(question)
        if model_identity_answer:
            yield from iter_result_events(
                result=QaKbExecutionResult(
                    success=True,
                    final_answer=model_identity_answer,
                    metadata=QaKbExecutionMetadata(
                        route="kb_qa",
                        pipeline_mode="new",
                        query_mode="model_identity_shortcut",
                        use_generation_driven=True,
                        stage_timings_ms=timings,
                    ),
                    raw={"shortcut": "model_identity"},
                ),
                sse_event=sse_event,
                chunk_size=chunk_size,
            )
            return

        yield sse_event({"type": "thinking", "content": "📝 阶段一：生成深度预回答与检索规划..."})
        stage1_result = self._timed(
            timings,
            "stage1",
            lambda: self._run_stage1(
                question=question,
                runtime=runtime,
                redis_service=redis_service,
                conversation_context=conversation_context,
                graph_evidence=graph_evidence,
                should_cancel=should_cancel,
            ),
        )
        logger.info(
            "fastqa stream stage1 returned success=%s keys=%s question=%s",
            stage1_result.get("success"),
            sorted(stage1_result.keys()),
            question[:120],
        )
        if not stage1_result.get("success"):
            yield sse_event(
                _build_fatal_error_event(
                    exc_or_state=stage1_result.get("upstream_error") or stage1_result.get("error"),
                    default_stage="stage1",
                    default_code="LLM_UNAVAILABLE",
                    default_error="llm_unavailable",
                )
            )
            return
        intent_step = self._intent_step_from_stage1(stage1_result)
        if intent_step:
            yield sse_event(intent_step)

        deep_answer = str(stage1_result.get("deep_answer") or "")
        answer_plan = stage1_result.get("answer_plan") if isinstance(stage1_result.get("answer_plan"), dict) else {}
        retrieval_claims = list(stage1_result.get("retrieval_claims") or [])
        retrieval_claims.extend(build_retrieval_claims_from_answer_plan(answer_plan))
        comparison_plan = build_comparison_plan(
            question,
            stage1_result=stage1_result,
            retrieval_claims=[item for item in retrieval_claims if isinstance(item, dict)],
        )
        comparison_plan = self._enhance_comparison_plan_with_profile(
            runtime=runtime,
            question=question,
            comparison_plan=comparison_plan,
            retrieval_claims=[item for item in retrieval_claims if isinstance(item, dict)],
            logger=logger,
        )
        if comparison_plan.get("enabled"):
            retrieval_claims = build_retrieval_claims_from_comparison_plan(comparison_plan) + build_retrieval_claims_from_answer_plan(answer_plan)
        stage1_query_focus_terms = effective_query_focus_terms_for_stage2(stage1_result)
        logger.info(
            "fastqa stream stage1 normalized deep_answer_chars=%s retrieval_claims=%s answer_plan_keys=%s "
            "comparison_enabled=%s query_focus_terms=%s question=%s",
            len(deep_answer),
            len(retrieval_claims),
            sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
            bool(comparison_plan.get("enabled")),
            stage1_query_focus_terms,
            _short_preview(question),
        )
        if not retrieval_claims:
            logger.warning(
                "fastqa stream short-circuit reason=stage1_no_retrieval_claims deep_answer_chars=%s "
                "answer_plan_keys=%s comparison_enabled=%s fallback=%s question=%s",
                len(deep_answer),
                sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
                bool(comparison_plan.get("enabled")),
                str(stage1_result.get("fallback") or ""),
                _short_preview(question),
            )
            yield sse_event(
                _degradation_warning_step(
                    step="stage1_no_retrieval_claims",
                    message="未生成检索要点，将仅使用阶段一预回答",
                )
            )
            yield from iter_result_events(
                result=self._fallback_result(
                    final_answer=deep_answer,
                    query_mode="生成驱动检索（仅预回答）",
                    timings=timings,
                    raw={
                        "deep_answer": deep_answer,
                        "retrieval_claims": retrieval_claims,
                        "stage1_result": stage1_result,
                        "answer_plan": answer_plan,
                        "comparison_plan": comparison_plan,
                    },
                ),
                sse_event=sse_event,
                chunk_size=chunk_size,
            )
            return

        logger.info("fastqa stream emitting stage2 thinking event question=%s", question[:120])
        yield sse_event({"type": "thinking", "content": "🔍 阶段二：检索高匹配度DOI..."})
        logger.info("fastqa stream entering stage2.run question=%s", question[:120])
        stage2_result = self._timed(
            timings,
            "stage2",
            lambda: self._run_stage2(
                question=question,
                runtime=runtime,
                retrieval_claims=retrieval_claims,
                redis_service=redis_service,
                n_results_per_claim=n_results_per_claim,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
                graph_evidence=graph_evidence,
                comparison_plan=comparison_plan,
                query_focus_terms=stage1_query_focus_terms,
            ),
        )
        logger.info(
            "fastqa stream stage2 returned success=%s unique_count=%s total_count=%s question=%s",
            stage2_result.get("success"),
            stage2_result.get("unique_count"),
            stage2_result.get("total_count"),
            question[:120],
        )
        if not stage2_result.get("success"):
            logger.warning(
                "fastqa stream fallback reason=stage2_failed error=%s cancelled=%s retrieval_claims=%s question=%s",
                str(stage2_result.get("error") or ""),
                bool(stage2_result.get("cancelled")),
                len(retrieval_claims),
                _short_preview(question),
            )
            yield sse_event(
                _build_fatal_error_event(
                    exc_or_state=stage2_result.get("upstream_error") or stage2_result.get("error"),
                    default_stage="stage2",
                    default_code=(
                        str(dict(stage2_result.get("upstream_error") or {}).get("code") or "RETRIEVAL_FAILED")
                    ),
                    default_error=str(dict(stage2_result.get("upstream_error") or {}).get("error") or "retrieval_failed"),
                )
            )
            return

        rerank_warning = _stage2_rerank_degradation_info(stage2_result)
        if rerank_warning:
            warning_data = {
                "code": "RERANK_DEGRADED",
                "failure_stage": "stage2",
                "reason": rerank_warning.get("reason") or "",
            }
            if rerank_warning.get("status_code") is not None:
                warning_data["status_code"] = int(rerank_warning["status_code"])
            yield sse_event(
                _degradation_warning_step(
                    step="stage2_rerank_fallback",
                    message=str(rerank_warning.get("message") or ""),
                    detail=str(rerank_warning.get("reason") or ""),
                    data=warning_data,
                )
            )

        dois = list(runtime._extract_dois_from_results(stage2_result))
        all_stage2_dois = _dedupe_preserve_order(dois)
        doi_source = "retrieval" if dois else "none"
        logger.info(
            "fastqa stream stage2 extracted doi_count=%s doi_sample=%s",
            len(dois),
            dois[:10],
        )
        if not dois and graph_evidence is not None and graph_evidence.stage2_doi_candidates:
            dois = self._dedupe_preserve_order(graph_evidence.stage2_doi_candidates)[: max(1, int(n_results_per_claim))]
            all_stage2_dois = list(dois)
            doi_source = "graph_seeded" if dois else "none"
            logger.info("fastqa stream graph-seeded doi fallback engaged doi_count=%s doi_sample=%s", len(dois), dois[:10])
        if dois:
            selected_dois = select_source_dois_for_evidence(
                retrieval_results=stage2_result,
                dois=dois,
                user_question=question,
                query_focus_terms=stage1_query_focus_terms,
            )
            if selected_dois != _dedupe_preserve_order(dois):
                logger.info(
                    "fastqa stream source doi gate reduced doi_count=%s->%s doi_sample=%s",
                    len(_dedupe_preserve_order(dois)),
                    len(selected_dois),
                    selected_dois[:10],
                )
            dois = selected_dois
            apply_selected_dois_to_comparison_groups(retrieval_results=stage2_result, selected_dois=dois)
        if not dois:
            logger.warning(
                "fastqa stream fallback reason=stage2_no_doi unique_count=%s total_count=%s "
                "all_stage2_dois=%s doi_source=%s query_focus_terms=%s question=%s",
                stage2_result.get("unique_count"),
                stage2_result.get("total_count"),
                len(all_stage2_dois),
                doi_source,
                stage1_query_focus_terms,
                _short_preview(question),
            )
            yield sse_event(
                _degradation_warning_step(
                    step="stage2_no_doi",
                    message="未检索到相关文献，将仅使用阶段一预回答",
                )
            )
            yield from iter_result_events(
                result=self._fallback_result(
                    final_answer=deep_answer,
                    query_mode="生成驱动检索（无DOI，仅预回答）",
                    timings=timings,
                    raw={
                        "deep_answer": deep_answer,
                        "retrieval_claims": retrieval_claims,
                        "retrieval_results": stage2_result,
                        "dois": [],
                        "all_stage2_dois": all_stage2_dois,
                        "doi_source": doi_source,
                        "comparison_plan": comparison_plan,
                    },
                ),
                sse_event=sse_event,
                chunk_size=chunk_size,
            )
            return

        if _stage3_diag_enabled():
            logger.info(
                "fastqa stream stage3 handoff doi_count=%s all_stage2_dois=%s doi_source=%s doi_sample=%s "
                "stage2_unique_count=%s stage2_total_count=%s query_focus_terms=%s",
                len(dois),
                len(all_stage2_dois),
                doi_source,
                list(dois or [])[:10],
                stage2_result.get("unique_count"),
                stage2_result.get("total_count"),
                stage1_query_focus_terms,
            )

        md_expansion_result = {
            "enabled": False,
            "applied": False,
            "md_chunks_by_doi": {},
            "stats": {"hit_doi_count": 0, "total_md_chunks": 0, "fallback_reason": ""},
        }
        try:
            yield sse_event({"type": "thinking", "content": "🧩 阶段二点五：尝试MD原文扩展检索..."})
            md_expansion_result = self._timed(
                timings,
                "stage25",
                lambda: self._run_stage25(
                    question=question,
                    runtime=runtime,
                    retrieval_results=stage2_result,
                    dois=dois,
                    redis_service=redis_service,
                    should_cancel=should_cancel,
                ),
            )
            logger.info(
                "fastqa stream stage25 completed applied=%s stats=%s",
                bool(md_expansion_result.get("applied")),
                dict(md_expansion_result.get("stats") or {}),
            )
            if md_expansion_result.get("applied"):
                md_stats = md_expansion_result.get("stats", {})
                yield sse_event(
                    {
                        "type": "thinking",
                        "content": (
                            "🧩 阶段二点五命中："
                            f"{md_stats.get('hit_doi_count', 0)} 个DOI，"
                            f"{md_stats.get('total_md_chunks', 0)} 个MD片段"
                        ),
                    }
                )
        except Exception as exc:
            logger.warning("stage25 md expansion failed, falling back to PDF path: %s", exc)
            yield sse_event(
                _degradation_warning_step(
                    step="stage25_md_expansion_failed",
                    message="MD 原文扩展失败，已回退 PDF 路径",
                )
            )

        skip_decision = self.evaluate_stage3_pdf_skip_fn(md_expansion_result=md_expansion_result)
        skip_pdf = bool(skip_decision.get("should_skip"))
        skip_reason = str(skip_decision.get("reason") or "")
        if _stage3_diag_enabled():
            logger.info(
                "fastqa stream stage3 skip decision skip_pdf=%s skip_reason=%s decision=%s md_applied=%s md_stats=%s",
                skip_pdf,
                skip_reason,
                dict(skip_decision or {}),
                bool(md_expansion_result.get("applied")),
                dict(md_expansion_result.get("stats") or {}),
            )
        if skip_pdf:
            pdf_chunks = dict(md_expansion_result.get("md_chunks_by_doi") or {})
            timings["stage3"] = 0.0
            yield sse_event(
                {
                    "type": "thinking",
                    "content": (
                        "📄 阶段三：MD证据命中阈值，跳过PDF溯源..."
                        f"（hit_doi={skip_decision.get('hit_doi_count', 0)}, "
                        f"md_chunks={skip_decision.get('total_md_chunks', 0)}）"
                    ),
                }
            )
        else:
            yield sse_event(
                {
                    "type": "thinking",
                    "content": f"📄 阶段三：加载 {len(dois)} 个文献的原文（提取 top 3 个最相关chunk）...",
                }
            )
            pdf_chunks = self._timed(
                timings,
                "stage3",
                lambda: self._run_stage3(
                    runtime=runtime,
                    dois=dois,
                    redis_service=redis_service,
                    max_chunks_per_doi=3,
                    should_cancel=should_cancel,
                ),
            )
            if md_expansion_result.get("applied") and self.merge_pdf_chunks_with_md_fn is not None and md_expansion_result.get("md_chunks_by_doi"):
                pdf_chunks = self.merge_pdf_chunks_with_md_fn(
                    pdf_chunks=pdf_chunks,
                    md_chunks=md_expansion_result.get("md_chunks_by_doi", {}),
                )

        if _stage3_diag_enabled():
            raw_source_count, raw_chunk_count = _evidence_counts(pdf_chunks)
            logger.info(
                "fastqa stream stage3 raw completed skip_pdf=%s skip_reason=%s source_count=%s chunk_count=%s "
                "stage25_applied=%s",
                skip_pdf,
                skip_reason,
                raw_source_count,
                raw_chunk_count,
                bool(md_expansion_result.get("applied")),
            )
        merge_before_source_count, merge_before_chunk_count = _evidence_counts(pdf_chunks)
        pdf_chunks = self._merge_stage2_into_evidence(
            pdf_chunks=pdf_chunks,
            retrieval_results=stage2_result,
            dois=dois,
            logger=logger,
        )
        if _stage3_diag_enabled():
            merge_after_source_count, merge_after_chunk_count = _evidence_counts(pdf_chunks)
            logger.info(
                "fastqa stream stage3 evidence merge completed before_sources=%s before_chunks=%s "
                "after_sources=%s after_chunks=%s",
                merge_before_source_count,
                merge_before_chunk_count,
                merge_after_source_count,
                merge_after_chunk_count,
            )

        logger.info(
            "fastqa stream stage3 completed skipped=%s skip_reason=%s pdf_source_count=%s pdf_chunk_count=%s",
            skip_pdf,
            skip_reason,
            len(pdf_chunks),
            sum(len(chunks) for chunks in pdf_chunks.values()),
        )
        yield sse_event({"type": "thinking", "content": "🔎 阶段3.5：重排候选证据chunk..."})
        evidence_rerank_result = self._timed(
            timings,
            "stage35",
            lambda: self._run_stage35_evidence_rerank(
                runtime=runtime,
                question=question,
                retrieval_results=stage2_result,
                pdf_chunks=pdf_chunks,
                logger=logger,
            ),
        )
        pdf_chunks = dict(evidence_rerank_result.get("pdf_chunks") or pdf_chunks)
        stage35_stats = dict(evidence_rerank_result.get("stats") or {})
        if stage35_stats.get("failed"):
            yield sse_event(
                _degradation_warning_step(
                    step="stage35_evidence_rerank_failed",
                    message="证据重排序失败，已使用未排序片段继续",
                )
            )
        logger.info(
            "fastqa stream stage35 completed stats=%s pdf_source_count=%s pdf_chunk_count=%s",
            dict(evidence_rerank_result.get("stats") or {}),
            len(pdf_chunks),
            sum(len(chunks) for chunks in pdf_chunks.values()),
        )
        yield sse_event({"type": "thinking", "content": "✍️ 阶段四：综合预回答与原文chunk生成答案..."})
        yield sse_event(
            {
                "type": "metadata",
                "route": "kb_qa",
                "pipeline_mode": "new",
                "use_generation_driven": 1,
                "stage3_pdf_skipped": skip_pdf,
                "stage3_pdf_skip_reason": skip_reason,
                "stage_timings_ms": timings,
                "stage35_evidence_rerank": dict(evidence_rerank_result.get("stats") or {}),
            }
        )

        logger.info(
            "fastqa stream stage4 starting pdf_source_count=%s pdf_chunk_count=%s",
            len(pdf_chunks),
            sum(len(chunks) for chunks in pdf_chunks.values()),
        )
        stage4_started = time.perf_counter()
        stage4_kwargs = {
            "runtime": runtime,
            "user_question": question,
            "deep_answer": deep_answer,
            "pdf_chunks": pdf_chunks,
            "retrieval_results": stage2_result,
            "should_cancel": should_cancel,
            "conversation_context": conversation_context,
        }
        if self._supports_kwarg(self.stage4.stream, "answer_plan"):
            stage4_kwargs["answer_plan"] = answer_plan
        if self._supports_kwarg(self.stage4.stream, "graph_evidence"):
            stage4_kwargs["graph_evidence"] = graph_evidence
        stage4_output = self.stage4.stream(**stage4_kwargs)

        final_chunks: list[str] = []
        final_result: dict[str, Any] | None = None
        for item in stage4_output:
            if isinstance(item, str):
                final_chunks.append(item)
                yield sse_event({"type": "content", "content": item})
            elif isinstance(item, dict):
                final_result = item

        timings["stage4"] = round((time.perf_counter() - stage4_started) * 1000, 3)

        if not final_result or not final_result.get("success"):
            logger.error(
                "fastqa stream stage4 failed error=%s partial_answer_chars=%s",
                (final_result or {}).get("error") if isinstance(final_result, dict) else None,
                len("".join(final_chunks).strip()),
            )
            yield sse_event(
                _build_fatal_error_event(
                    exc_or_state=(
                        dict(final_result or {}).get("upstream_error")
                        or dict(final_result or {}).get("error")
                        or final_result
                    ),
                    default_stage="stage4",
                    default_code="LLM_UNAVAILABLE",
                    default_error="llm_unavailable",
                )
            )
            return

        final_answer = str(final_result.get("final_answer") or "".join(final_chunks))
        references = final_result.get("references")
        logger.info(
            "fastqa stream stage4 succeeded final_answer_chars=%s references=%s cited_dois=%s timings=%s",
            len(final_answer),
            len(references if isinstance(references, list) else []),
            len(final_result.get("cited_dois") or []),
            timings,
        )
        yield sse_event(
            {
                "type": "done",
                "query_mode": _final_query_mode(provided=final_result.get("query_mode"), skip_pdf=skip_pdf),
                "route": "kb_qa",
                "doi_source": doi_source,
                "doi_count": len(dois),
                "chunk_count": sum(len(chunks) for chunks in pdf_chunks.values()),
                "source_count": len(pdf_chunks),
                "final_answer": final_answer,
                "timings": timings,
                "references": references if isinstance(references, list) else [],
                "metadata": {
                    "route": "kb_qa",
                    "query_mode": _final_query_mode(provided=final_result.get("query_mode"), skip_pdf=skip_pdf),
                    "pipeline_mode": "new",
                    "doi_source": doi_source,
                },
            }
        )
