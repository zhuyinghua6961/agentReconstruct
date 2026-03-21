#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF QA orchestration helpers."""

from __future__ import annotations

from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event, Thread
import time
from typing import Any, Callable, Iterator, Optional

from app.modules.qa_pdf.prompting import (
    GENERIC_PHRASES,
    PDF_QA_SYSTEM_MESSAGE,
    build_kb_section,
    build_pdf_answer_prompt,
    is_summary_question,
)
from app.modules.qa_pdf.truncation import (
    smart_truncate_pdf_content as smart_truncate_pdf_content_impl,
)

LLM_NOT_READY_MESSAGE = "抱歉，当前问答模型未就绪（LLM 未初始化），请稍后重试。"


class PDFQAFirstTokenTimeoutError(RuntimeError):
    """Raised when stream first token is not received before deadline."""


class PDFQAStreamCancelledError(RuntimeError):
    """Raised when PDF streaming is cancelled by caller."""


def smart_truncate_pdf_content(
    pdf_content: str,
    max_chars: int,
    *,
    logger: Any,
    is_summary: bool = False,
    question: str = "",
) -> str:
    """智能截断 PDF 内容，优先保留重要章节。"""
    return smart_truncate_pdf_content_impl(
        pdf_content=pdf_content,
        max_chars=max_chars,
        logger=logger,
        is_summary=is_summary,
        question=question,
    )


def answer_from_pdf(
    question: str,
    pdf_content: str,
    *,
    llm: Any,
    max_pdf_chars: int,
    smart_truncate_fn: Callable[..., str],
    logger: Any,
    traceback_module: Any,
    kb_verification: Optional[dict] = None,
    stream: bool = False,
    first_token_timeout_sec: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Any:
    """基于 PDF 内容回答问题，可选结合知识库验证。"""
    def _cancelled() -> bool:
        if is_cancelled is None:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    def _normalize_first_token_timeout(value: float | None) -> float:
        if value is None:
            return 25.0
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 25.0
        if parsed < 1.0:
            return 1.0
        if parsed > 180.0:
            return 180.0
        return parsed

    stream_first_token_timeout_sec = _normalize_first_token_timeout(first_token_timeout_sec)

    def _log_timing(stage: str, started_at: float, **fields: Any) -> None:
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        extras = " ".join(f"{k}={fields[k]}" for k in fields if fields[k] is not None)
        extra_text = f" {extras}" if extras else ""
        logger.info(
            "⏱️ [PDF_QA_TIMING] "
            f"ts={datetime.now().isoformat(timespec='milliseconds')} "
            f"stage={stage} elapsed_ms={elapsed_ms:.2f}{extra_text}"
        )

    qa_started_at = time.monotonic()
    logger.info(
        "⏱️ [PDF_QA_TIMING] "
        f"ts={datetime.now().isoformat(timespec='milliseconds')} "
        f"stage=qa_start question_chars={len(str(question or ''))} "
        f"pdf_chars={len(str(pdf_content or ''))}"
    )

    if not pdf_content or len(pdf_content.strip()) < 100:
        logger.error(f"❌ PDF内容无效或过短: {len(pdf_content) if pdf_content else 0} 字符")
        return "**错误**：PDF内容提取失败或内容过少，无法回答问题。请检查PDF文件是否可读。"

    if pdf_content.startswith("[错误]"):
        logger.error(f"❌ PDF提取失败: {pdf_content}")
        return f"**错误**：{pdf_content}"

    if llm is None or not hasattr(llm, "invoke"):
        logger.error("❌ PDF问答失败: LLM不可用（未初始化或不支持invoke）")
        return LLM_NOT_READY_MESSAGE

    summary_mode = is_summary_question(question)

    pdf_content_to_use = pdf_content
    if len(pdf_content) > max_pdf_chars:
        truncate_started_at = time.monotonic()
        pdf_content_to_use = smart_truncate_fn(
            pdf_content,
            max_pdf_chars,
            is_summary=summary_mode,
            question=question,
        )
        _log_timing(
            "prompt_truncate_done",
            truncate_started_at,
            original_chars=len(pdf_content),
            truncated_chars=len(pdf_content_to_use),
        )
    else:
        logger.info(f"✅ PDF内容长度合适（{len(pdf_content)}字符），无需截断")
        _log_timing(
            "prompt_truncate_skipped",
            qa_started_at,
            original_chars=len(pdf_content),
            truncated_chars=len(pdf_content_to_use),
        )

    preview = pdf_content_to_use[:500].replace("\n", " ")
    logger.info(f"📄 PDF内容预览（前500字符）: {preview}...")
    logger.info(f"📊 PDF内容长度: {len(pdf_content_to_use)} 字符")

    prompt_started_at = time.monotonic()
    kb_section = build_kb_section(kb_verification)
    prompt = build_pdf_answer_prompt(
        question=question,
        pdf_content=pdf_content_to_use,
        kb_section=kb_section,
        is_summary=summary_mode,
    )
    _log_timing(
        "prompt_build_done",
        prompt_started_at,
        prompt_chars=len(prompt),
        kb_section_chars=len(kb_section),
    )

    def _iter_stream_answer() -> Iterator[str]:
        messages = [
            {"role": "system", "content": PDF_QA_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]

        emitted = False
        total_chunks = 0
        total_chars = 0
        stream_started_at = time.monotonic()
        logger.info(
            "⏱️ [PDF_QA_TIMING] "
            f"ts={datetime.now().isoformat(timespec='milliseconds')} stage=llm_request_start mode=stream"
        )
        try:
            if hasattr(llm, "stream"):
                logger.info("🚀 PDF流式LLM请求已发起")
                stream_queue: Queue[tuple[str, Any]] = Queue(maxsize=64)
                producer_stop = Event()

                def _producer() -> None:
                    producer_started_at = time.monotonic()
                    _log_timing("llm_stream_producer_started", stream_started_at)
                    try:
                        _log_timing("llm_stream_provider_iter_call_start", producer_started_at)
                        first_chunk_enqueued = False
                        for chunk in llm.stream(messages):
                            if producer_stop.is_set():
                                break
                            while not producer_stop.is_set():
                                try:
                                    stream_queue.put(("chunk", chunk), timeout=0.1)
                                    if not first_chunk_enqueued:
                                        chunk_content = getattr(chunk, "content", "")
                                        _log_timing(
                                            "llm_stream_producer_first_chunk_enqueued",
                                            producer_started_at,
                                            first_chunk_chars=len(str(chunk_content or "")),
                                        )
                                        first_chunk_enqueued = True
                                    break
                                except Full:
                                    continue
                    except Exception as exc:
                        _log_timing(
                            "llm_stream_producer_error",
                            producer_started_at,
                            error_type=type(exc).__name__,
                        )
                        try:
                            stream_queue.put(("error", exc), timeout=0.1)
                        except Exception:
                            pass
                    finally:
                        _log_timing("llm_stream_producer_finished", producer_started_at)
                        try:
                            stream_queue.put(("end", None), timeout=0.1)
                        except Exception:
                            pass

                producer = Thread(target=_producer, name="pdf-qa-stream-producer", daemon=True)
                producer.start()
                _log_timing("llm_stream_producer_spawned", stream_started_at)

                first_token_deadline = stream_started_at + stream_first_token_timeout_sec
                while True:
                    if _cancelled():
                        producer_stop.set()
                        _log_timing("llm_stream_cancelled", stream_started_at, emitted=int(emitted))
                        raise PDFQAStreamCancelledError("PDF stream cancelled")

                    queue_timeout = 0.2
                    if not emitted:
                        remaining = first_token_deadline - time.monotonic()
                        if remaining <= 0:
                            producer_stop.set()
                            _log_timing(
                                "llm_first_token_timeout",
                                stream_started_at,
                                timeout_sec=stream_first_token_timeout_sec,
                            )
                            raise PDFQAFirstTokenTimeoutError(
                                f"首包等待超时（>{stream_first_token_timeout_sec:.1f}s）"
                            )
                        queue_timeout = min(queue_timeout, max(0.05, remaining))

                    try:
                        event_type, payload = stream_queue.get(timeout=queue_timeout)
                    except Empty:
                        continue

                    if event_type == "error":
                        producer_stop.set()
                        raise payload
                    if event_type == "end":
                        _log_timing("llm_stream_consumer_received_end", stream_started_at, emitted=int(emitted))
                        break
                    if event_type != "chunk":
                        continue

                    content = getattr(payload, "content", "")
                    if not content:
                        continue
                    if not emitted:
                        _log_timing(
                            "llm_stream_consumer_first_chunk_received",
                            stream_started_at,
                            first_chunk_chars=len(str(content)),
                        )
                        _log_timing(
                            "llm_first_token",
                            stream_started_at,
                            first_token_chars=len(str(content)),
                        )
                    emitted = True
                    total_chunks += 1
                    total_chars += len(str(content))
                    yield str(content)
        except (PDFQAFirstTokenTimeoutError, PDFQAStreamCancelledError):
            raise
        except Exception as exc:
            logger.warning(f"⚠️ PDF流式回答失败，回退非流式: {exc}")

        if emitted:
            _log_timing(
                "llm_stream_complete",
                stream_started_at,
                total_chunks=total_chunks,
                total_chars=total_chars,
            )
            _log_timing("qa_complete_stream", qa_started_at, answer_chars=total_chars)
            return

        try:
            logger.info("↩️ PDF流式无输出，回退非流式invoke")
            invoke_started_at = time.monotonic()
            llm_result = llm.invoke(messages)
            answer = getattr(llm_result, "content", llm_result)
            answer = str(answer or "")
            if answer:
                _log_timing("llm_invoke_done", invoke_started_at, answer_chars=len(answer))
                yield answer
                _log_timing("qa_complete_invoke_fallback", qa_started_at, answer_chars=len(answer))
            return
        except Exception as exc:
            logger.error(f"❌ PDF问答失败: {exc}")
            logger.error(traceback_module.format_exc())
            try:
                fallback_started_at = time.monotonic()
                fallback_result = llm.invoke(prompt)
                fallback_answer = getattr(fallback_result, "content", fallback_result)
                fallback_answer = str(fallback_answer or "")
                if fallback_answer:
                    _log_timing("llm_invoke_prompt_fallback_done", fallback_started_at, answer_chars=len(fallback_answer))
                    yield fallback_answer
                    _log_timing("qa_complete_invoke_prompt_fallback", qa_started_at, answer_chars=len(fallback_answer))
                return
            except Exception as fallback_exc:
                logger.error(f"❌ PDF问答失败（回退方式）: {fallback_exc}")
                if llm is None or not hasattr(llm, "invoke"):
                    yield LLM_NOT_READY_MESSAGE
                    return
                yield f"抱歉，处理PDF内容时出错：{str(fallback_exc)}"
                return

    if stream:
        return _iter_stream_answer()

    try:
        messages = [
            {"role": "system", "content": PDF_QA_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]
        invoke_started_at = time.monotonic()
        logger.info(
            "⏱️ [PDF_QA_TIMING] "
            f"ts={datetime.now().isoformat(timespec='milliseconds')} stage=llm_request_start mode=invoke"
        )
        llm_result = llm.invoke(messages)
        answer = getattr(llm_result, "content", llm_result)
        answer = str(answer or "")
        _log_timing("llm_invoke_done", invoke_started_at, answer_chars=len(answer))
        _log_timing("qa_complete_invoke", qa_started_at, answer_chars=len(answer))

        answer_lower = answer.lower()
        has_generic = any(phrase in answer_lower for phrase in GENERIC_PHRASES)
        if has_generic:
            logger.warning("⚠️ 答案中可能包含通用知识，请检查PDF内容是否完整")
            logger.warning(f"   PDF内容长度: {len(pdf_content_to_use)} 字符")
            logger.warning(f"   答案预览: {answer[:200]}...")
        return answer
    except Exception as exc:
        logger.error(f"❌ PDF问答失败: {exc}")
        logger.error(traceback_module.format_exc())
        try:
            fallback_started_at = time.monotonic()
            fallback_result = llm.invoke(prompt)
            fallback_answer = getattr(fallback_result, "content", fallback_result)
            fallback_answer = str(fallback_answer or "")
            _log_timing("llm_invoke_prompt_fallback_done", fallback_started_at, answer_chars=len(fallback_answer))
            _log_timing("qa_complete_invoke_prompt_fallback", qa_started_at, answer_chars=len(fallback_answer))
            return fallback_answer
        except Exception as fallback_exc:
            logger.error(f"❌ PDF问答失败（回退方式）: {fallback_exc}")
            if llm is None or not hasattr(llm, "invoke"):
                return LLM_NOT_READY_MESSAGE
            return f"抱歉，处理PDF内容时出错：{str(fallback_exc)}"
