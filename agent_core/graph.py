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
import time
import asyncio
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

import config
from agent_core.direct_answerer import direct_answer
from agent_core.decomposer import decompose_question
from agent_core.llm_client import get_async_llm_client, get_llm_client
from agent_core.sub_answerer import iter_pre_answers_async, pre_answer_all
from agent_core.synthesizer import synthesize_answer, synthesize_answer_stream
from agent_core.checker import check_answer
from agent_core.reviser import revise_answer
from retriever.vector_retriever import batch_retrieve, RetrievedChunk
from ingest.embedder import get_embedding_client
from ingest.vector_store import get_or_create_collection

logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """Agent 运行状态"""
    # 输入
    question: str = ""

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
) -> tuple[list[str], list[list[RetrievedChunk]], dict[str, float]]:
    """流水线执行 Step2 预回答和 Step3 检索。"""

    async def _pipeline() -> tuple[list[str], list[list[RetrievedChunk]], dict[str, float]]:
        sub_answers = [""] * len(sub_questions)
        retrieved_chunks: list[list[RetrievedChunk]] = [[] for _ in sub_questions]
        pending_batch: list[tuple[int, str]] = []
        retrieval_jobs: list[tuple[list[int], concurrent.futures.Future]] = []
        metrics = {
            "pre_answer_completed_at": 0.0,
            "retrieval_completed_at": 0.0,
        }
        started_at = time.time()
        resolved_batch_size = max(1, int(batch_size))
        completed_pre_answers = 0

        def _submit_batch(executor: concurrent.futures.ThreadPoolExecutor) -> None:
            nonlocal pending_batch
            if not pending_batch:
                return
            batch_items = pending_batch
            pending_batch = []
            batch_indexes = [item[0] for item in batch_items]
            batch_queries = [item[1] for item in batch_items]
            future = executor.submit(
                batch_retrieve,
                batch_queries,
                retrieval_top_k,
                collection,
                embedding_client,
            )
            retrieval_jobs.append((batch_indexes, future))

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as retrieval_executor:
            async for index, answer in iter_pre_answers_async(
                sub_questions,
                async_client=async_llm_client,
            ):
                completed_pre_answers += 1
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
                    _submit_batch(retrieval_executor)

            metrics["pre_answer_completed_at"] = time.time() - started_at
            _submit_batch(retrieval_executor)

            for indexes, future in retrieval_jobs:
                batch_results = await asyncio.wrap_future(future, loop=loop)
                for idx, chunks in zip(indexes, batch_results):
                    retrieved_chunks[idx] = chunks

        metrics["retrieval_completed_at"] = time.time() - started_at
        return sub_answers, retrieved_chunks, metrics

    return asyncio.run(_pipeline())


def run_agent(
    question: str,
    stream_callback: Optional[Callable[[str], None]] = None,
    step_callback: Optional[Callable[[str, float], None]] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    enable_thinking: Optional[bool] = None,
    num_sub_questions: Optional[int] = None,
    retrieval_top_k: Optional[int] = None,
    max_check_loops: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
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
    state = AgentState(question=question)
    total_start = time.time()
    resolved_enable_thinking = config.LLM_ENABLE_THINKING if enable_thinking is None else bool(enable_thinking)
    resolved_direct_answer_enable_thinking = (
        config.DIRECT_ANSWER_ENABLE_THINKING if enable_thinking is None else bool(enable_thinking)
    )
    resolved_decompose_enable_thinking = (
        config.DECOMPOSE_ENABLE_THINKING if enable_thinking is None else bool(enable_thinking)
    )
    resolved_num_sub_questions = int(num_sub_questions) if num_sub_questions is not None else int(config.NUM_SUB_QUESTIONS)
    resolved_retrieval_top_k = int(retrieval_top_k) if retrieval_top_k is not None else int(config.RETRIEVAL_TOP_K)
    resolved_max_check_loops = int(max_check_loops) if max_check_loops is not None else int(config.MAX_CHECK_LOOPS)
    resolved_retrieval_pipeline_batch_size = int(config.RETRIEVAL_PIPELINE_BATCH_SIZE)
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
        direct_llm_client = get_llm_client()
        pipeline_llm_client = get_llm_client()
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
            answer = direct_answer(
                question,
                client=direct_llm_client,
                enable_thinking=resolved_direct_answer_enable_thinking,
            )
            return answer, time.time() - started_at

        def _timed_decompose_question() -> tuple[list[str], float]:
            started_at = time.time()
            questions = decompose_question(
                question,
                client=pipeline_llm_client,
                num_sub_questions=resolved_num_sub_questions,
                enable_thinking=resolved_decompose_enable_thinking,
            )
            return questions, time.time() - started_at

        # 使用线程并行（因为两个都是同步调用外部 API）
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_direct = executor.submit(_timed_direct_answer)
            future_decompose = executor.submit(_timed_decompose_question)

            state.sub_questions, decompose_elapsed = future_decompose.result()
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
            collection = get_or_create_collection()
            state.sub_answers, state.retrieved_chunks, pipeline_metrics = _run_pre_answer_retrieval_pipeline(
                sub_questions=state.sub_questions,
                retrieval_top_k=resolved_retrieval_top_k,
                async_llm_client=async_llm_client,
                collection=collection,
                embedding_client=embedding_client,
                batch_size=resolved_retrieval_pipeline_batch_size,
                progress_callback=progress_callback,
            )
            _raise_if_cancelled()
            state.retrieval_queries = [
                f"{q}\n{a}" if a else q
                for q, a in zip(state.sub_questions, state.sub_answers)
            ]

            state.direct_answer, direct_elapsed = future_direct.result()
            _emit_progress(
                "step1",
                "running",
                "直接回答完成，进入综合阶段",
                direct_answer_chars=len(state.direct_answer),
                direct_elapsed_seconds=round(float(direct_elapsed), 3),
            )

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
            for chunk in synthesize_answer_stream(
                question=state.question,
                direct_answer=state.direct_answer,
                all_retrieved_chunks=state.retrieved_chunks,
                sub_questions=state.sub_questions,
                client=pipeline_llm_client,
                enable_thinking=resolved_enable_thinking,
            ):
                _raise_if_cancelled()
                if chunk:
                    draft_chunks.append(chunk)
                    stream_callback(chunk)
            state.draft_answer = "".join(draft_chunks)
        else:
            state.draft_answer = synthesize_answer(
                question=state.question,
                direct_answer=state.direct_answer,
                all_retrieved_chunks=state.retrieved_chunks,
                sub_questions=state.sub_questions,
                client=pipeline_llm_client,
                enable_thinking=resolved_enable_thinking,
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
                passed, issues = check_answer(
                    question=state.question,
                    answer=current_answer,
                    all_retrieved_chunks=state.retrieved_chunks,
                    client=pipeline_llm_client,
                )
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
                current_answer = revise_answer(
                    question=state.question,
                    answer=current_answer,
                    issues=issues,
                    client=pipeline_llm_client,
                )
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
        logger.error(f"Agent 运行出错: {e}", exc_info=True)
        state.error = str(e)

    state.timings["total"] = time.time() - total_start
    logger.info(f"Agent 总耗时: {state.timings['total']:.1f}s")

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
