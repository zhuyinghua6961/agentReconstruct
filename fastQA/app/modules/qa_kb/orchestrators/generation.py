from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric
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
from app.modules.qa_kb.models import GenerationRuntime, QaKbExecutionMetadata, QaKbExecutionResult
from app.modules.qa_kb.stages.pdf_loading import Stage3PdfLoader
from app.modules.qa_kb.stages.planning import Stage1Planner
from app.modules.qa_kb.stages.retrieval import Stage25MdExpansion, Stage2Retriever
from app.modules.qa_kb.stages.synthesis import Stage4Synthesizer
from app.modules.qa_kb.streaming import iter_result_events


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




def _final_query_mode(*, provided: Any, skip_pdf: bool) -> str:
    value = str(provided or "").strip()
    if value:
        return value
    return "生成驱动检索（MD直读）" if skip_pdf else "生成驱动检索（PDF溯源）"
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
    ) -> None:
        self.stage1 = stage1 or Stage1Planner()
        self.stage2 = stage2 or Stage2Retriever()
        self.stage25 = stage25 or Stage25MdExpansion()
        self.stage3 = stage3 or Stage3PdfLoader()
        self.stage4 = stage4 or Stage4Synthesizer()
        self.evaluate_stage3_pdf_skip_fn = evaluate_stage3_pdf_skip_fn or (lambda **_kwargs: {"should_skip": False, "reason": ""})
        self.merge_pdf_chunks_with_md_fn = merge_pdf_chunks_with_md_fn

    def _timed(self, timings: dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        result = fn()
        timings[key] = round((time.perf_counter() - started) * 1000, 3)
        return result

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
                stage_timings_ms=timings,
            ),
            raw=raw,
        )

    def _run_stage1(
        self,
        *,
        question: str,
        runtime: GenerationRuntime,
        redis_service: RedisService | None,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cached = get_cached_stage1_result(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            conversation_context=conversation_context,
        )
        if cached is not None:
            increment_cache_metric("stage1", "cache_hit")
            return cached
        increment_cache_metric("stage1", "cache_miss")

        def _compute() -> dict[str, Any]:
            result = self.stage1.run(
                runtime=runtime,
                user_question=question,
                conversation_context=conversation_context,
            )
            cache_stage1_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                stage1_result=result,
                conversation_context=conversation_context,
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
            ),
            namespace="stage1",
            read_cached_fn=lambda: get_cached_stage1_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                conversation_context=conversation_context,
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
    ) -> dict[str, Any]:
        cached = get_cached_stage2_result(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
        )
        if cached is not None:
            increment_cache_metric("stage2", "cache_hit")
            return cached
        increment_cache_metric("stage2", "cache_miss")

        def _compute() -> dict[str, Any]:
            result = self.stage2.run(
                runtime=runtime,
                retrieval_claims=retrieval_claims,
                n_results_per_claim=n_results_per_claim,
                user_question=question,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
            )
            cache_stage2_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_claims=retrieval_claims,
                n_results_per_claim=n_results_per_claim,
                stage2_result=result,
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
            ),
            namespace="stage2",
            read_cached_fn=lambda: get_cached_stage2_result(
                redis_service=redis_service,
                runtime=runtime,
                question=question,
                retrieval_claims=retrieval_claims,
                n_results_per_claim=n_results_per_claim,
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
    ) -> dict[str, Any]:
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

    def _run_stage3(
        self,
        *,
        runtime: GenerationRuntime,
        dois: list[str],
        redis_service: RedisService | None,
        max_chunks_per_doi: int,
        should_cancel: Callable[[], bool] | None,
    ) -> dict[str, list[dict[str, Any]]]:
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
        retrieval_claims = list(stage1_result.get("retrieval_claims") or [])
        if not retrieval_claims:
            return self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（仅预回答）",
                timings=timings,
                raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "stage1_result": stage1_result},
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
            ),
        )
        if not stage2_result.get("success"):
            return self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（检索失败，仅预回答）",
                timings=timings,
                raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "retrieval_results": stage2_result},
            )

        dois = list(runtime._extract_dois_from_results(stage2_result))
        logger.info(
            "fastqa stream stage2 extracted doi_count=%s doi_sample=%s",
            len(dois),
            dois[:10],
        )
        if not dois:
            return self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（无DOI，仅预回答）",
                timings=timings,
                raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "retrieval_results": stage2_result, "dois": []},
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
                ),
            )
        except Exception as exc:
            logger.warning("stage25 md expansion failed, falling back to PDF path: %s", exc)

        skip_decision = self.evaluate_stage3_pdf_skip_fn(md_expansion_result=md_expansion_result)
        skip_pdf = bool(skip_decision.get("should_skip"))
        skip_reason = str(skip_decision.get("reason") or "")

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

        return {
            "timings": timings,
            "deep_answer": deep_answer,
            "retrieval_claims": retrieval_claims,
            "retrieval_results": stage2_result,
            "dois": dois,
            "pdf_chunks": pdf_chunks,
            "md_expansion": md_expansion_result,
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
        )
        if isinstance(prepared, QaKbExecutionResult):
            return prepared

        stage4_output = self._timed(
            prepared["timings"],
            "stage4",
            lambda: self.stage4.stream(
                runtime=runtime,
                user_question=question,
                deep_answer=prepared["deep_answer"],
                pdf_chunks=prepared["pdf_chunks"],
                retrieval_results=prepared["retrieval_results"],
                should_cancel=should_cancel,
            ),
        )
        synthesis_result = _consume_stage4_result(stage4_output, logger)
        if not synthesis_result.get("success"):
            return self._fallback_result(
                final_answer=prepared["deep_answer"],
                query_mode="生成驱动检索（合成失败，仅预回答）",
                timings=prepared["timings"],
                raw=prepared,
            )

        return QaKbExecutionResult(
            success=True,
            final_answer=str(synthesis_result.get("final_answer") or ""),
            metadata=QaKbExecutionMetadata(
                route="kb_qa",
                pipeline_mode="new",
                query_mode=_final_query_mode(provided=synthesis_result.get("query_mode"), skip_pdf=prepared["skip_pdf"]),
                use_generation_driven=True,
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
            ),
        )
        logger.info(
            "fastqa stream stage1 returned success=%s keys=%s question=%s",
            stage1_result.get("success"),
            sorted(stage1_result.keys()),
            question[:120],
        )
        if not stage1_result.get("success"):
            yield sse_event({"type": "error", "error": stage1_result.get("error", "阶段一失败")})
            return

        deep_answer = str(stage1_result.get("deep_answer") or "")
        retrieval_claims = list(stage1_result.get("retrieval_claims") or [])
        logger.info(
            "fastqa stream stage1 normalized deep_answer_chars=%s retrieval_claims=%s question=%s",
            len(deep_answer),
            len(retrieval_claims),
            question[:120],
        )
        if not retrieval_claims:
            yield from iter_result_events(
                result=self._fallback_result(
                    final_answer=deep_answer,
                    query_mode="生成驱动检索（仅预回答）",
                    timings=timings,
                    raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "stage1_result": stage1_result},
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
            yield from iter_result_events(
                result=self._fallback_result(
                    final_answer=deep_answer,
                    query_mode="生成驱动检索（检索失败，仅预回答）",
                    timings=timings,
                    raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "retrieval_results": stage2_result},
                ),
                sse_event=sse_event,
                chunk_size=chunk_size,
            )
            return

        dois = list(runtime._extract_dois_from_results(stage2_result))
        logger.info(
            "fastqa stream stage2 extracted doi_count=%s doi_sample=%s",
            len(dois),
            dois[:10],
        )
        if not dois:
            yield from iter_result_events(
                result=self._fallback_result(
                    final_answer=deep_answer,
                    query_mode="生成驱动检索（无DOI，仅预回答）",
                    timings=timings,
                    raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "retrieval_results": stage2_result, "dois": []},
                ),
                sse_event=sse_event,
                chunk_size=chunk_size,
            )
            return

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

        skip_decision = self.evaluate_stage3_pdf_skip_fn(md_expansion_result=md_expansion_result)
        skip_pdf = bool(skip_decision.get("should_skip"))
        skip_reason = str(skip_decision.get("reason") or "")
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

        logger.info(
            "fastqa stream stage3 completed skipped=%s skip_reason=%s pdf_source_count=%s pdf_chunk_count=%s",
            skip_pdf,
            skip_reason,
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
            }
        )

        logger.info(
            "fastqa stream stage4 starting pdf_source_count=%s pdf_chunk_count=%s",
            len(pdf_chunks),
            sum(len(chunks) for chunks in pdf_chunks.values()),
        )
        stage4_started = time.perf_counter()
        stage4_output = self.stage4.stream(
            runtime=runtime,
            user_question=question,
            deep_answer=deep_answer,
            pdf_chunks=pdf_chunks,
            retrieval_results=stage2_result,
            should_cancel=should_cancel,
        )

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
            fallback = self._fallback_result(
                final_answer=deep_answer,
                query_mode="生成驱动检索（合成失败，仅预回答）",
                timings=timings,
                raw={"deep_answer": deep_answer, "retrieval_claims": retrieval_claims, "retrieval_results": stage2_result, "dois": dois, "pdf_chunks": pdf_chunks},
            )
            if not final_chunks:
                for event in iter_result_events(result=fallback, sse_event=sse_event, chunk_size=chunk_size):
                    payload = event if isinstance(event, dict) else None
                    if payload is None or payload.get("type") != "metadata":
                        yield event
            else:
                yield sse_event(
                    {
                        "type": "done",
                        "query_mode": fallback.metadata.query_mode,
                        "route": fallback.metadata.route,
                        "doi_count": 0,
                        "chunk_count": 0,
                        "source_count": 0,
                        "final_answer": "".join(final_chunks).strip() or fallback.final_answer,
                        "timings": timings,
                        "references": [],
                    }
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
                "doi_count": len(dois),
                "chunk_count": sum(len(chunks) for chunks in pdf_chunks.values()),
                "source_count": len(pdf_chunks),
                "final_answer": final_answer,
                "timings": timings,
                "references": references if isinstance(references, list) else [],
            }
        )
