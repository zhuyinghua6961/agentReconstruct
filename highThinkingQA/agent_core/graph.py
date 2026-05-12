"""
LangGraph 流程编排
双路并行 + 综合 + Checker-Reviser 引用验证的完整 Agentic RAG 流程：

  用户输入问题
      ├── 路径A: LLM 直接回答（含思考）
      └── 路径B: 分解 -> 预回答（含思考） -> 检索
                          ↓
              综合 LLM（合并 A + B） -> 草稿答案
                          ↓
              Checker-Reviser 循环 -> 最终答案
"""

import concurrent.futures
import logging
import os
import time
import asyncio
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

import config
from agent_core.direct_answerer import direct_answer
from agent_core.decomposer import decompose_question
from agent_core.answer_summary import apply_answer_summary_experiment, summary_experiment_enabled
from agent_core.llm_client import get_async_llm_client, get_llm_client
from agent_core.sub_answerer import iter_pre_answers_async, pre_answer_all
from agent_core.synthesizer import synthesize_answer, synthesize_answer_stream
from agent_core.doi_diagnostics import build_preanswer_blob, doi_diagnostics_enabled, log_doi_trace
from agent_core.checker import CheckerTimeoutError, check_answer
from agent_core.reviser import ReviserTimeoutError, revise_answer
from retriever.vector_retriever import batch_retrieve, RetrievedChunk
from ingest.embedder import get_embedding_client
from ingest.vector_store import get_or_create_collection
from server.services.stage_cache import get_or_compute_decompose, get_or_compute_direct_answer

logger = logging.getLogger(__name__)
_PARTIAL_RETRIEVAL_FLUSH_WAIT_SECONDS = 0.8
_CHECKER_WALL_CLOCK_TIMEOUT_SECONDS = 60.0
_REVISER_WALL_CLOCK_TIMEOUT_SECONDS = 60.0
_CANCEL_WAIT_INTERVAL_SECONDS = 0.05


def _trace_prefix(trace_id: str | None) -> str:
    token = str(trace_id or "").strip()
    return f"[trace_id={token}] " if token else ""


def _short_text(value: str, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _call_with_wall_clock_timeout(
    *,
    func: Callable[..., Any],
    timeout_seconds: float,
    timeout_error: Exception,
    cancel_event: threading.Event | None = None,
    cancel_error: Exception | None = None,
    **kwargs: Any,
) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        deadline = time.monotonic() + max(0.001, float(timeout_seconds))
        while True:
            if cancel_event is not None and cancel_event.is_set():
                future.cancel()
                raise cancel_error or RuntimeError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise timeout_error
            try:
                return future.result(timeout=min(_CANCEL_WAIT_INTERVAL_SECONDS, max(0.001, remaining)))
            except concurrent.futures.TimeoutError as exc:
                if future.done():
                    raise timeout_error from exc
                continue
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise timeout_error from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _future_result_with_cancel(
    future: concurrent.futures.Future,
    *,
    cancel_event: threading.Event | None,
    interval_seconds: float = _CANCEL_WAIT_INTERVAL_SECONDS,
) -> Any:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            future.cancel()
            raise RuntimeError("cancelled")
        try:
            return future.result(timeout=max(0.001, float(interval_seconds)))
        except concurrent.futures.TimeoutError:
            continue


@dataclass
class AgentState:
    """Agent 运行状态"""
    # 输入
    question: str = ""
    raw_question: str = ""
    effective_question: str = ""
    conversation_context: dict[str, Any] = field(default_factory=dict)

    # 路径 A
    direct_answer: str = ""

    # 路径 B
    sub_questions: list[str] = field(default_factory=list)
    sub_answers: list[str] = field(default_factory=list)
    retrieval_queries: list[str] = field(default_factory=list)
    retrieved_chunks: list[list[RetrievedChunk]] = field(default_factory=list)

    # Step 4 综合草稿
    draft_answer: str = ""

    # Step 5 Check-Revise 结果
    check_passed: bool = False
    check_loops: int = 0
    check_issues: list[dict] = field(default_factory=list)

    # 最终输出
    final_answer: str = ""

    # 运行元信息
    timings: dict = field(default_factory=dict)
    error: str = ""


def _run_pre_answer_retrieval_pipeline(
    *,
    sub_questions: list[str],
    retrieval_top_k: int,
    async_llm_client,
    collection,
    embedding_client,
    batch_size: int,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    trace_id: Optional[str] = None,
    original_question: str | None = None,
) -> tuple[list[str], list[list[RetrievedChunk]], dict[str, float]]:
    """流水线执行 Step2 预回答和 Step3 检索。"""

    def _raise_if_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("cancelled")

    async def _pipeline() -> tuple[list[str], list[list[RetrievedChunk]], dict[str, float]]:
        sub_answers = [""] * len(sub_questions)
        retrieved_chunks: list[list[RetrievedChunk]] = [[] for _ in sub_questions]
        pending_batch: list[tuple[int, str]] = []
        retrieval_jobs: list[tuple[int, list[int], asyncio.Future]] = []
        total_batches = max(1, (len(sub_questions) + max(1, int(batch_size)) - 1) // max(1, int(batch_size)))
        submitted_retrieval_batches = 0
        completed_retrieval_batches = 0
        delayed_flush_task: asyncio.Task | None = None
        metrics = {
            "pre_answer_completed_at": 0.0,
            "retrieval_completed_at": 0.0,
            "retrieval_total_batches": float(total_batches),
        }
        started_at = time.time()
        resolved_batch_size = max(1, int(batch_size))
        completed_pre_answers = 0

        def _cancel_delayed_flush() -> None:
            nonlocal delayed_flush_task
            if delayed_flush_task is not None and not delayed_flush_task.done():
                delayed_flush_task.cancel()
            delayed_flush_task = None

        def _submit_batch(executor: concurrent.futures.ThreadPoolExecutor, *, reason: str) -> None:
            nonlocal pending_batch, submitted_retrieval_batches, total_batches
            if not pending_batch:
                return
            batch_items = pending_batch
            pending_batch = []
            batch_indexes = [item[0] for item in batch_items]
            batch_queries = [item[1] for item in batch_items]
            announced_total_batches = max(total_batches, submitted_retrieval_batches + 1)
            logger.info(
                "%sstep3 submit retrieval batch %s/%s reason=%s indexes=%s query_chars=%s",
                _trace_prefix(trace_id),
                submitted_retrieval_batches + 1,
                announced_total_batches,
                reason,
                batch_indexes,
                [len(item) for item in batch_queries],
            )
            future = executor.submit(
                batch_retrieve,
                batch_queries,
                retrieval_top_k,
                collection,
                embedding_client,
            )
            submitted_retrieval_batches += 1
            total_batches = max(total_batches, submitted_retrieval_batches)
            retrieval_jobs.append(
                (
                    submitted_retrieval_batches,
                    batch_indexes,
                    asyncio.wrap_future(future, loop=loop),
                )
            )
            if progress_callback:
                progress_callback(
                    {
                        "type": "progress",
                        "stage": "step3",
                        "status": "running",
                        "message": f"已提交 {submitted_retrieval_batches}/{total_batches} 批文献检索任务",
                        "data": {
                            "submitted_batches": submitted_retrieval_batches,
                            "total_batches": total_batches,
                            "batch_queries": len(batch_queries),
                            "total_queries": len(sub_questions),
                        },
                    }
                )

        async def _flush_partial_batch_after_wait(executor: concurrent.futures.ThreadPoolExecutor) -> None:
            try:
                await asyncio.sleep(_PARTIAL_RETRIEVAL_FLUSH_WAIT_SECONDS)
                if pending_batch and len(pending_batch) < resolved_batch_size:
                    logger.info(
                        "%sstep3 flush partial retrieval batch size=%s wait=%.3fs",
                        _trace_prefix(trace_id),
                        len(pending_batch),
                        _PARTIAL_RETRIEVAL_FLUSH_WAIT_SECONDS,
                    )
                    _submit_batch(executor, reason="partial_timeout")
            except asyncio.CancelledError:
                return

        def _schedule_partial_flush(executor: concurrent.futures.ThreadPoolExecutor) -> None:
            nonlocal delayed_flush_task
            if len(pending_batch) >= resolved_batch_size:
                return
            if delayed_flush_task is not None and not delayed_flush_task.done():
                return
            delayed_flush_task = asyncio.create_task(_flush_partial_batch_after_wait(executor))

        loop = asyncio.get_running_loop()
        retrieval_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            _raise_if_cancelled()
            async for index, answer in iter_pre_answers_async(
                sub_questions,
                async_client=async_llm_client,
                original_question=original_question,
            ):
                _raise_if_cancelled()
                completed_pre_answers += 1
                logger.info(
                    "%sstep2 sub-question answered index=%s/%s answer_chars=%s question=%s",
                    _trace_prefix(trace_id),
                    index + 1,
                    len(sub_questions),
                    len(answer or ""),
                    _short_text(sub_questions[index]),
                )
                sub_answers[index] = answer
                if progress_callback:
                    progress_callback(
                        {
                            "type": "progress",
                            "stage": "step2",
                            "status": "running",
                            "message": f"子问题预回答完成 {index + 1}/{len(sub_questions)}",
                            "data": {
                                "completed": completed_pre_answers,
                                "total": len(sub_questions),
                                "sub_question_index": index,
                            },
                        }
                    )
                query = f"{sub_questions[index]}\n{answer}" if answer else sub_questions[index]
                pending_batch.append((index, query))
                if len(pending_batch) >= resolved_batch_size:
                    _cancel_delayed_flush()
                    _submit_batch(retrieval_executor, reason="batch_full")
                else:
                    _schedule_partial_flush(retrieval_executor)

            _raise_if_cancelled()
            metrics["pre_answer_completed_at"] = time.time() - started_at
            if progress_callback:
                progress_callback(
                    {
                        "type": "progress",
                        "stage": "step2",
                        "status": "success",
                        "message": "子问题预回答全部完成，开始等待检索结果",
                        "data": {
                            "completed": completed_pre_answers,
                            "total": len(sub_questions),
                        },
                    }
                )
            _cancel_delayed_flush()
            _submit_batch(retrieval_executor, reason="drain")

            pending_retrievals = {
                wrapped_future: (batch_no, indexes)
                for batch_no, indexes, wrapped_future in retrieval_jobs
            }
            while pending_retrievals:
                _raise_if_cancelled()
                done, _ = await asyncio.wait(
                    pending_retrievals.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=_CANCEL_WAIT_INTERVAL_SECONDS,
                )
                if not done:
                    continue
                for wrapped_future in done:
                    _raise_if_cancelled()
                    batch_no, indexes = pending_retrievals.pop(wrapped_future)
                    logger.info(
                        "%sstep3 awaiting retrieval batch indexes=%s",
                        _trace_prefix(trace_id),
                        indexes,
                    )
                    batch_results = wrapped_future.result()
                    completed_retrieval_batches += 1
                    for idx, chunks in zip(indexes, batch_results):
                        retrieved_chunks[idx] = chunks
                    logger.info(
                        "%sstep3 retrieval batch completed %s/%s batch_no=%s indexes=%s chunk_counts=%s",
                        _trace_prefix(trace_id),
                        completed_retrieval_batches,
                        total_batches,
                        batch_no,
                        indexes,
                        [len(chunks) for chunks in batch_results],
                    )
                    if progress_callback:
                        progress_callback(
                            {
                                "type": "progress",
                                "stage": "step3",
                                "status": "running",
                                "message": f"文献检索已完成 {completed_retrieval_batches}/{total_batches} 批",
                                "data": {
                                    "completed_batches": completed_retrieval_batches,
                                    "total_batches": total_batches,
                                    "retrieved_chunks_total": sum(len(chunks) for chunks in retrieved_chunks),
                                },
                            }
                        )
        finally:
            _cancel_delayed_flush()
            retrieval_executor.shutdown(wait=False, cancel_futures=True)

        metrics["retrieval_completed_at"] = time.time() - started_at
        metrics["retrieval_total_batches"] = float(total_batches)
        return sub_answers, retrieved_chunks, metrics

    return asyncio.run(_pipeline())


def run_agent(
    question: str,
    stream_callback: Optional[Callable[[str], None]] = None,
    step_callback: Optional[Callable[[str, float], None]] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    raw_question: Optional[str] = None,
    conversation_context: Optional[dict[str, Any]] = None,
    enable_thinking: Optional[bool] = None,
    num_sub_questions: Optional[int] = None,
    retrieval_top_k: Optional[int] = None,
    max_check_loops: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    trace_id: Optional[str] = None,
) -> AgentState:
    """
    运行完整的 Agentic RAG 流程（同步入口）。

    流程:
    1. 并行执行路径 A（直接回答）和路径 B 第一步（查询分解）
    2. 路径 B: 并行预回答 5 个子问题
    3. 路径 B: 构建检索 query 并并行检索
    4. 综合所有信息生成草稿答案（非流式）
    5. Checker-Reviser 循环验证引用准确性（最多循环 MAX_CHECK_LOOPS 次）

    Args:
        question: 用户输入的问题
        stream_callback: 流式输出回调函数。验证完成后将最终答案
                         通过 callback(text_chunk) 输出。
        step_callback: 步骤进度回调。每步完成后调用
                       callback(step_description, elapsed_seconds)。

    Returns:
        包含完整运行结果的 AgentState
    """
    state = AgentState(
        question=str(raw_question or question),
        raw_question=str(raw_question or question),
        effective_question=str(question),
        conversation_context=dict(conversation_context or {}),
    )
    total_start = time.time()
    working_question = state.effective_question or state.question
    resolved_enable_thinking = config.MAIN_LLM_THINKING_ENABLED if enable_thinking is None else bool(enable_thinking)
    resolved_stream_synthesis_enable_thinking = False if stream_callback else resolved_enable_thinking
    resolved_direct_answer_enable_thinking = (
        config.DIRECT_STAGE_THINKING_ENABLED if enable_thinking is None else bool(enable_thinking)
    )
    resolved_decompose_enable_thinking = (
        config.DECOMPOSE_STAGE_THINKING_ENABLED if enable_thinking is None else bool(enable_thinking)
    )
    resolved_num_sub_questions = int(num_sub_questions) if num_sub_questions is not None else int(config.NUM_SUB_QUESTIONS)
    resolved_retrieval_top_k = int(retrieval_top_k) if retrieval_top_k is not None else int(config.RETRIEVAL_TOP_K)
    resolved_max_check_loops = int(max_check_loops) if max_check_loops is not None else int(config.MAX_CHECK_LOOPS)
    resolved_retrieval_pipeline_batch_size = int(config.RETRIEVAL_PIPELINE_BATCH_SIZE)
    resolved_summary_experiment = summary_experiment_enabled()
    if resolved_num_sub_questions <= 0:
        resolved_num_sub_questions = 1
    if resolved_retrieval_top_k <= 0:
        resolved_retrieval_top_k = 1
    if resolved_max_check_loops < 0:
        resolved_max_check_loops = 0
    if resolved_retrieval_pipeline_batch_size <= 0:
        resolved_retrieval_pipeline_batch_size = 1

    def _raise_if_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("cancelled")

    def _emit_progress(stage: str, status: str, message: str, **data: Any) -> None:
        if not progress_callback:
            return
        payload = {
            "type": "progress",
            "stage": stage,
            "status": status,
            "message": message,
        }
        if data:
            payload["data"] = data
        progress_callback(payload)

    try:
        logger.info(
            "%srun_agent start raw_question=%s effective_question=%s",
            _trace_prefix(trace_id),
            _short_text(state.raw_question),
            _short_text(state.effective_question),
        )
        direct_llm_client = get_llm_client()
        pipeline_llm_client = get_llm_client()
        verification_llm_client = get_llm_client(max_retries=0)
        async_llm_client = get_async_llm_client()
        embedding_client = get_embedding_client()

        # ============================================================
        # Step 1: 并行执行路径 A (直接回答) 和路径 B 第一步 (查询分解)
        # ============================================================
        logger.info("Step 1: 并行执行直接回答 + 查询分解")
        _emit_progress("step1", "started", "开始执行直接回答与查询分解")
        t0 = time.time()

        direct_elapsed: float | None = None
        decompose_elapsed: float | None = None

        def _timed_direct_answer() -> tuple[str, float]:
            started_at = time.time()
            logger.info("%sstep1 direct_answer start", _trace_prefix(trace_id))
            answer = get_or_compute_direct_answer(
                question=working_question,
                model=config.LLM_MODEL,
                enable_thinking=resolved_direct_answer_enable_thinking,
                compute_fn=lambda: direct_answer(
                    working_question,
                    client=direct_llm_client,
                    enable_thinking=resolved_direct_answer_enable_thinking,
                ),
            )
            elapsed = time.time() - started_at
            logger.info(
                "%sstep1 direct_answer done elapsed=%.3fs answer_chars=%s",
                _trace_prefix(trace_id),
                elapsed,
                len(answer or ""),
            )
            return answer, elapsed

        def _timed_decompose_question() -> tuple[list[str], float]:
            started_at = time.time()
            logger.info("%sstep1 decompose start", _trace_prefix(trace_id))
            questions = get_or_compute_decompose(
                question=working_question,
                model=config.LLM_MODEL,
                enable_thinking=resolved_decompose_enable_thinking,
                num_sub_questions=resolved_num_sub_questions,
                compute_fn=lambda: decompose_question(
                    working_question,
                    client=pipeline_llm_client,
                    num_sub_questions=resolved_num_sub_questions,
                    enable_thinking=resolved_decompose_enable_thinking,
                ),
            )
            elapsed = time.time() - started_at
            logger.info(
                "%sstep1 decompose done elapsed=%.3fs sub_questions=%s",
                _trace_prefix(trace_id),
                elapsed,
                len(questions),
            )
            return questions, elapsed

        # 使用线程并行（因为两个都是同步调用外部 API）
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        try:
            future_collection = executor.submit(get_or_create_collection)
            future_direct = executor.submit(_timed_direct_answer)
            future_decompose = executor.submit(_timed_decompose_question)

            state.sub_questions, decompose_elapsed = _future_result_with_cancel(
                future_decompose,
                cancel_event=cancel_event,
            )
            _emit_progress(
                "step1",
                "running",
                "查询分解完成，开始组织子问题",
                sub_questions=len(state.sub_questions),
                decompose_elapsed_seconds=round(float(decompose_elapsed), 3),
            )

            # ============================================================
            # Step 2: 子问题预回答与检索
            # 在直接回答仍在运行时，先启动路径 B，避免无谓等待。
            # ============================================================
            logger.info("Step 2/3: 子问题预回答 + 检索流水线")
            _emit_progress("step2", "started", "开始执行子问题预回答与检索流水线")
            t_step23 = time.time()
            if not future_collection.done():
                logger.info("%sstep3 waiting for collection warmup", _trace_prefix(trace_id))
            collection = _future_result_with_cancel(
                future_collection,
                cancel_event=cancel_event,
            )
            state.sub_answers, state.retrieved_chunks, pipeline_metrics = _run_pre_answer_retrieval_pipeline(
                sub_questions=state.sub_questions,
                retrieval_top_k=resolved_retrieval_top_k,
                async_llm_client=async_llm_client,
                collection=collection,
                embedding_client=embedding_client,
                batch_size=resolved_retrieval_pipeline_batch_size,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                trace_id=trace_id,
                original_question=working_question,
            )
            _raise_if_cancelled()
            state.retrieval_queries = [
                f"{q}\n{a}" if a else q
                for q, a in zip(state.sub_questions, state.sub_answers)
            ]

            if not future_direct.done():
                logger.info("%sstep1 waiting for direct answer after retrieval pipeline", _trace_prefix(trace_id))
                _emit_progress(
                    "step1",
                    "running",
                    "子问题处理完成，等待直接回答收尾",
                    sub_questions=len(state.sub_questions),
                    pre_answers=len(state.sub_answers),
                )
            try:
                state.direct_answer, direct_elapsed = _future_result_with_cancel(
                    future_direct,
                    cancel_event=cancel_event,
                )
            except TimeoutError as exc:
                direct_elapsed = 0.0
                state.direct_answer = "直接回答超时，已改用检索结果生成答案。"
                logger.warning(
                    "%sstep1 direct_answer failed, continuing with retrieval-only synthesis: %s",
                    _trace_prefix(trace_id),
                    exc,
                )
                _emit_progress(
                    "step1",
                    "warning",
                    "直接回答超时，继续使用检索结果生成答案",
                    fallback=True,
                    error=str(exc),
                )
            _emit_progress(
                "step1",
                "running",
                "直接回答完成，进入综合阶段",
                direct_answer_chars=len(state.direct_answer),
                direct_elapsed_seconds=round(float(direct_elapsed), 3),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        _raise_if_cancelled()
        state.timings["step1_parallel"] = max(float(direct_elapsed or 0.0), float(decompose_elapsed or 0.0))
        logger.info(
            f"Step 1 完成 ({state.timings['step1_parallel']:.1f}s): "
            f"直接回答 {len(state.direct_answer)} chars, "
            f"{len(state.sub_questions)} 个子问题"
        )
        if step_callback:
            step_callback(
                f"Step 1 完成: 直接回答 + 查询分解 ({len(state.sub_questions)} 个子问题)",
                state.timings["step1_parallel"],
            )

        state.timings["step2_pre_answer"] = pipeline_metrics["pre_answer_completed_at"]
        logger.info(
            f"Step 2 完成 ({state.timings['step2_pre_answer']:.1f}s): "
            f"{len(state.sub_answers)} 个预回答"
        )
        if step_callback:
            step_callback(
                f"Step 2 完成: {len(state.sub_answers)} 个子问题预回答",
                state.timings["step2_pre_answer"],
            )

        total_chunks = sum(len(chunks) for chunks in state.retrieved_chunks)
        _emit_progress(
            "step3",
            "success",
            f"文献检索完成，共获得 {total_chunks} 个文段",
            completed_batches=int(pipeline_metrics.get("retrieval_total_batches") or 1),
            total_batches=int(pipeline_metrics.get("retrieval_total_batches") or 1),
            retrieved_chunks_total=total_chunks,
        )
        state.timings["step3_retrieval"] = pipeline_metrics["retrieval_completed_at"]
        logger.info(
            f"Step 3 完成 ({state.timings['step3_retrieval']:.1f}s): "
            f"共检索到 {total_chunks} 个文段"
        )
        if step_callback:
            step_callback(
                f"Step 3 完成: 检索到 {total_chunks} 个文献文段",
                state.timings["step3_retrieval"],
            )

        # ============================================================
        # Step 4: 综合生成草稿答案（始终非流式）
        # ============================================================
        logger.info("Step 4: 综合生成草稿答案")
        _emit_progress("step4", "started", "开始综合生成草稿答案")
        t0 = time.time()

        if stream_callback:
            draft_chunks: list[str] = []
            draft_stream_started = False
            logger.info("%sstep4 synthesis stream start", _trace_prefix(trace_id))
            for chunk in synthesize_answer_stream(
                question=working_question,
                direct_answer=state.direct_answer,
                all_retrieved_chunks=state.retrieved_chunks,
                sub_questions=state.sub_questions,
                client=pipeline_llm_client,
                enable_thinking=resolved_stream_synthesis_enable_thinking,
                summary_enabled=resolved_summary_experiment,
            ):
                _raise_if_cancelled()
                if chunk:
                    if not draft_stream_started:
                        logger.info("%sstep4 synthesis first_chunk chars=%s", _trace_prefix(trace_id), len(chunk))
                        _emit_progress("step4", "running", "综合草稿开始流式输出", chunk_index=1)
                        draft_stream_started = True
                    draft_chunks.append(chunk)
                    stream_callback(chunk)
            state.draft_answer = "".join(draft_chunks)
            logger.info(
                "%sstep4 synthesis stream done total_chars=%s",
                _trace_prefix(trace_id),
                len(state.draft_answer),
            )
        else:
            logger.info("%sstep4 synthesis blocking start", _trace_prefix(trace_id))
            state.draft_answer = synthesize_answer(
                question=working_question,
                direct_answer=state.direct_answer,
                all_retrieved_chunks=state.retrieved_chunks,
                sub_questions=state.sub_questions,
                client=pipeline_llm_client,
                enable_thinking=resolved_enable_thinking,
                summary_enabled=resolved_summary_experiment,
            )
            logger.info(
                "%sstep4 synthesis blocking done total_chars=%s",
                _trace_prefix(trace_id),
                len(state.draft_answer),
            )
        _raise_if_cancelled()

        state.timings["step4_synthesis"] = time.time() - t0
        logger.info(
            f"Step 4 完成 ({state.timings['step4_synthesis']:.1f}s): "
            f"草稿答案 {len(state.draft_answer)} chars"
        )
        if step_callback:
            step_callback(
                f"Step 4 完成: 综合生成草稿答案 ({len(state.draft_answer)} chars)",
                state.timings["step4_synthesis"],
            )

        if doi_diagnostics_enabled():
            log_doi_trace(
                logger,
                trace_prefix=_trace_prefix(trace_id),
                phase="draft_after_step4",
                answer_text=state.draft_answer,
                pre_blob=build_preanswer_blob(state.direct_answer, state.sub_answers),
                all_chunks=state.retrieved_chunks,
            )

        # ============================================================
        # Step 5: Checker-Reviser 引用验证循环
        # ============================================================
        logger.info("Step 5: Checker-Reviser 引用验证")
        t0 = time.time()
        step5_check_total = 0.0
        step5_revise_total = 0.0
        step5_issue_total = 0
        step5_revise_rounds = 0

        current_answer = state.draft_answer
        if resolved_max_check_loops == 0:
            state.check_passed = True
            state.check_issues = []
            state.check_loops = 0
            logger.info("Step 5 - 当前配置跳过 Checker-Reviser")
            _emit_progress(
                "step5_check",
                "success",
                "当前配置跳过引用检查",
                check_loop=0,
                issues=0,
                elapsed_seconds=0.0,
            )
        else:
            for loop_i in range(resolved_max_check_loops):
                _raise_if_cancelled()
                logger.info(f"Step 5 - 第 {loop_i + 1} 轮检查")
                _emit_progress(
                    "step5_check",
                    "started",
                    f"开始第 {loop_i + 1} 轮引用检查",
                    check_loop=loop_i + 1,
                    max_check_loops=resolved_max_check_loops,
                )

                check_started_at = time.time()
                try:
                    passed, issues = _call_with_wall_clock_timeout(
                        func=check_answer,
                        timeout_seconds=_CHECKER_WALL_CLOCK_TIMEOUT_SECONDS,
                        timeout_error=CheckerTimeoutError("checker llm request timed out"),
                        cancel_event=cancel_event,
                        cancel_error=RuntimeError("cancelled"),
                        question=working_question,
                        answer=current_answer,
                        all_retrieved_chunks=state.retrieved_chunks,
                        client=verification_llm_client,
                    )
                except (CheckerTimeoutError, TimeoutError) as exc:
                    check_elapsed = time.time() - check_started_at
                    step5_check_total += check_elapsed
                    state.check_passed = False
                    state.check_issues = []
                    state.check_loops = loop_i + 1
                    state.timings[f"step5_check_loop_{loop_i + 1}"] = check_elapsed
                    logger.warning(
                        "%sStep 5 - 第 %s 轮检查超时，保留当前答案并结束检查: %s",
                        _trace_prefix(trace_id),
                        loop_i + 1,
                        exc,
                    )
                    _emit_progress(
                        "step5_check",
                        "error",
                        f"第 {loop_i + 1} 轮引用检查超时，保留当前答案并结束检查",
                        check_loop=loop_i + 1,
                        issues=0,
                        elapsed_seconds=round(float(check_elapsed), 3),
                        error=str(exc),
                    )
                    break
                check_elapsed = time.time() - check_started_at
                step5_check_total += check_elapsed
                state.check_passed = passed
                state.check_issues = issues
                state.check_loops = loop_i + 1
                step5_issue_total += len(issues)
                state.timings[f"step5_check_loop_{loop_i + 1}"] = check_elapsed

                if passed:
                    logger.info(f"Step 5 - 第 {loop_i + 1} 轮检查通过")
                    _emit_progress(
                        "step5_check",
                        "success",
                        f"第 {loop_i + 1} 轮引用检查通过",
                        check_loop=loop_i + 1,
                        issues=0,
                        elapsed_seconds=round(float(check_elapsed), 3),
                    )
                    break

                logger.info(
                    f"Step 5 - 第 {loop_i + 1} 轮检查未通过，"
                    f"发现 {len(issues)} 个问题，交由 Reviser 修改"
                )
                for idx, issue in enumerate(issues[:12]):
                    if not isinstance(issue, dict):
                        continue
                    logger.info(
                        "%sStep 5 - checker issue detail [%s/%s] problem=%s citation=%s",
                        _trace_prefix(trace_id),
                        idx + 1,
                        len(issues),
                        issue.get("problem"),
                        issue.get("citation"),
                    )
                _emit_progress(
                    "step5_check",
                    "success",
                    f"第 {loop_i + 1} 轮发现 {len(issues)} 个引用问题",
                    check_loop=loop_i + 1,
                    issues=len(issues),
                    elapsed_seconds=round(float(check_elapsed), 3),
                )
                _emit_progress(
                    "step5_revise",
                    "started",
                    f"开始第 {loop_i + 1} 轮问题修订",
                    check_loop=loop_i + 1,
                    issues=len(issues),
                )

                revise_started_at = time.time()
                try:
                    current_answer = _call_with_wall_clock_timeout(
                        func=revise_answer,
                        timeout_seconds=_REVISER_WALL_CLOCK_TIMEOUT_SECONDS,
                        timeout_error=ReviserTimeoutError("reviser llm request timed out"),
                        cancel_event=cancel_event,
                        cancel_error=RuntimeError("cancelled"),
                        question=working_question,
                        answer=current_answer,
                        issues=issues,
                        client=verification_llm_client,
                    )
                except (ReviserTimeoutError, TimeoutError) as exc:
                    revise_elapsed = time.time() - revise_started_at
                    step5_revise_total += revise_elapsed
                    state.timings[f"step5_revise_loop_{loop_i + 1}"] = revise_elapsed
                    logger.warning(
                        "%sStep 5 - 第 %s 轮修订超时，保留当前答案并结束检查: %s",
                        _trace_prefix(trace_id),
                        loop_i + 1,
                        exc,
                    )
                    _emit_progress(
                        "step5_revise",
                        "error",
                        f"第 {loop_i + 1} 轮问题修订超时，保留当前答案并结束检查",
                        check_loop=loop_i + 1,
                        issues=len(issues),
                        elapsed_seconds=round(float(revise_elapsed), 3),
                        error=str(exc),
                    )
                    break
                revise_elapsed = time.time() - revise_started_at
                step5_revise_total += revise_elapsed
                step5_revise_rounds += 1
                state.timings[f"step5_revise_loop_{loop_i + 1}"] = revise_elapsed
                _raise_if_cancelled()
                _emit_progress(
                    "step5_revise",
                    "success",
                    f"第 {loop_i + 1} 轮问题修订完成",
                    check_loop=loop_i + 1,
                    issues=len(issues),
                    elapsed_seconds=round(float(revise_elapsed), 3),
                )

            if not state.check_passed:
                logger.info(
                    f"Step 5 - 已达最大循环次数 ({resolved_max_check_loops})，强制输出"
                )

        state.final_answer = current_answer
        state.final_answer, summary_meta = apply_answer_summary_experiment(
            state.final_answer,
            enabled=resolved_summary_experiment,
        )
        if doi_diagnostics_enabled():
            log_doi_trace(
                logger,
                trace_prefix=_trace_prefix(trace_id),
                phase="final_after_step5",
                answer_text=state.final_answer,
                pre_blob=build_preanswer_blob(state.direct_answer, state.sub_answers),
                all_chunks=state.retrieved_chunks,
            )
        logger.info(
            "%sanswer summary experiment enabled=%s generated=%s format=%s length=%s has_citation=%s skipped_reason=%s",
            _trace_prefix(trace_id),
            summary_meta.get("enabled"),
            summary_meta.get("generated"),
            summary_meta.get("format"),
            summary_meta.get("length"),
            summary_meta.get("has_citation"),
            summary_meta.get("skipped_reason"),
        )

        state.timings["step5_check_total"] = step5_check_total
        state.timings["step5_revise_total"] = step5_revise_total
        state.timings["step5_issue_total"] = float(step5_issue_total)
        state.timings["step5_revise_rounds"] = float(step5_revise_rounds)
        state.timings["step5_check_revise"] = time.time() - t0
        check_status = "通过" if state.check_passed else "强制输出"
        logger.info(
            f"Step 5 完成 ({state.timings['step5_check_revise']:.1f}s): "
            f"循环 {state.check_loops} 次, {check_status}, "
            f"最终答案 {len(state.final_answer)} chars"
        )
        if step_callback:
            step_callback(
                f"Step 5 完成: 引用验证 {state.check_loops} 轮, {check_status}",
                state.timings["step5_check_revise"],
            )

    except Exception as e:
        logger.error("%sAgent 运行出错: %s", _trace_prefix(trace_id), e, exc_info=True)
        state.error = str(e)

    state.timings["total"] = time.time() - total_start
    logger.info("%sAgent 总耗时: %.1fs", _trace_prefix(trace_id), state.timings["total"])

    return state


def format_state_summary(
    state: AgentState,
    include_final_answer: bool = True,
) -> str:
    """
    格式化 AgentState 为可读的摘要文本。

    Args:
        state: Agent 运行状态
        include_final_answer: 是否包含最终答案（流式输出时设为 False）

    Returns:
        格式化的摘要文本
    """
    lines = [
        "=" * 60,
        "Agentic RAG 运行摘要",
        "=" * 60,
        f"\n原始问题: {state.question}\n",
    ]
    if state.effective_question and state.effective_question != state.question:
        lines.append(f"执行问题: {state.effective_question}\n")

    if state.error:
        lines.append(f"错误: {state.error}\n")

    lines.append("--- 路径 A: 直接回答 ---")
    lines.append(f"{state.direct_answer[:500]}...\n" if len(state.direct_answer) > 500 else f"{state.direct_answer}\n")

    lines.append("--- 路径 B: 子问题分解 ---")
    for i, (q, a) in enumerate(zip(state.sub_questions, state.sub_answers)):
        lines.append(f"  Q{i+1}: {q}")
        lines.append(f"  A{i+1}: {a[:200]}..." if len(a) > 200 else f"  A{i+1}: {a}")
        chunks = state.retrieved_chunks[i] if i < len(state.retrieved_chunks) else []
        lines.append(f"  检索到 {len(chunks)} 个文段")
        lines.append("")

    lines.append("--- 引用检查 (Checker-Reviser) ---")
    lines.append(f"  检查轮次: {state.check_loops}")
    lines.append(f"  最终通过: {'是' if state.check_passed else '否（强制输出）'}")
    if state.check_issues:
        lines.append(f"  最后一轮问题数: {len(state.check_issues)}")
    lines.append("")

    lines.append("--- 耗时统计 ---")
    for key, val in state.timings.items():
        lines.append(f"  {key}: {val:.1f}s")

    if include_final_answer:
        lines.append("\n" + "=" * 60)
        lines.append("最终答案")
        lines.append("=" * 60)
        lines.append(state.final_answer)

    return "\n".join(lines)
