from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from app.integrations.llm import raise_if_upstream_pool_timeout


def _emit(payload: dict[str, Any], sse_event: Any) -> Any:
    if callable(sse_event):
        return sse_event(payload)
    return payload


def _extract_doi_from_pdf_filename(pdf_path: str) -> str:
    text = str(pdf_path or "").strip()
    if not text:
        return ""
    if "." in text.rsplit("/", 1)[-1]:
        stem, suffix = text.rsplit(".", 1)
        if suffix.lower() == "pdf":
            text = stem
    match = re.search(r"(10\.\d+[/_][-._;()/:A-Za-z0-9]+)", text)
    if not match:
        return ""
    return match.group(1).replace("_", "/", 1).rstrip(").,;")


def _iter_answer_pieces(answer: Any) -> Iterator[str]:
    if isinstance(answer, str):
        if answer:
            yield answer
        return
    if isinstance(answer, Iterable):
        for item in answer:
            text = str(item or "")
            if text:
                yield text
        return
    text = str(answer or "")
    if text:
        yield text


def _run_kb_verification(
    *,
    question: str,
    agent: Any,
    performance_mode: str,
    executor: Any,
    timeout_error_cls: Any,
) -> dict[str, Any] | None:
    if agent is None or not hasattr(agent, "smart_query"):
        return None
    use_dual = str(performance_mode or "").strip().lower() == "quality"
    try:
        if executor is not None and timeout_error_cls is not None:
            future = executor.submit(agent.smart_query, question, use_dual_retrieval=use_dual)
            try:
                result = future.result(timeout=8)
            except timeout_error_cls:
                future.cancel()
                return None
        else:
            result = agent.smart_query(question, use_dual_retrieval=use_dual)
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        return None
    if not isinstance(result, dict) or not result.get("success") or not result.get("final_answer"):
        return None
    return {
        "kb_answer": str(result.get("final_answer") or ""),
        "kb_data": result.get("raw_data", []),
        "query_mode": str(result.get("query_mode") or "未知"),
    }


def iter_uploaded_pdf_answer_events(**kwargs: Any) -> Iterator[Any]:
    question = str(kwargs.get("question") or "").strip()
    pdf_path = str(kwargs.get("pdf_path") or "").strip()
    pdf_content = str(kwargs.get("pdf_content") or "")
    performance_mode = str(kwargs.get("performance_mode") or "balanced")
    allow_kb_verification = bool(kwargs.get("allow_kb_verification"))
    agent = kwargs.get("agent")
    executor = kwargs.get("executor")
    timeout_error_cls = kwargs.get("timeout_error_cls")
    sse_event = kwargs.get("sse_event")
    answer_from_pdf_fn = kwargs.get("answer_from_pdf_fn")
    clean_answer_for_frontend = kwargs.get("clean_answer_for_frontend") or (lambda text: text)
    filter_literature_markers_for_streaming = kwargs.get("filter_literature_markers_for_streaming") or (lambda text: text)
    log_qa_interaction = kwargs.get("log_qa_interaction") or (lambda **_kwargs: None)
    cache_key_mode = str(kwargs.get("cache_key_mode") or "").strip()
    cache_key_question = str(kwargs.get("cache_key_question") or "").strip()
    cache_set_fn = kwargs.get("cache_set_fn") or (lambda *_args, **_kwargs: None)
    is_cancelled = kwargs.get("is_cancelled")
    logger = kwargs.get("logger")

    def _cancelled() -> bool:
        if not callable(is_cancelled):
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    if _cancelled():
        return

    query_mode = "PDF文献查询"
    kb_verification = None
    if allow_kb_verification:
        kb_verification = _run_kb_verification(
            question=question,
            agent=agent,
            performance_mode=performance_mode,
            executor=executor,
            timeout_error_cls=timeout_error_cls,
        )
        if kb_verification is not None:
            query_mode += " + 知识库验证"

    yield _emit({"type": "metadata", "query_mode": query_mode}, sse_event)
    yield _emit({"type": "thinking", "content": "📄 正在分析上传的PDF文献..."}, sse_event)
    if kb_verification is not None:
        yield _emit({"type": "thinking", "content": "🔍 已完成知识库验证，正在组织答案..."}, sse_event)
    yield _emit({"type": "thinking", "content": "✍️ 正在生成答案..."}, sse_event)

    if not callable(answer_from_pdf_fn):
        yield _emit({"type": "error", "error": "pdf_answer_backend_unavailable"}, sse_event)
        return

    try:
        answer_output = answer_from_pdf_fn(
            question,
            pdf_content,
            kb_verification=kb_verification,
            stream=True,
            first_token_timeout_sec=None,
            is_cancelled=is_cancelled,
        )
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        if logger is not None:
            logger.warning("PDF QA invocation failed: %s", exc)
        yield _emit({"type": "error", "error": str(exc) or "pdf_qa_failed"}, sse_event)
        return

    raw_parts: list[str] = []
    for piece in _iter_answer_pieces(answer_output):
        if _cancelled():
            return
        raw_parts.append(piece)
        filtered_piece = str(filter_literature_markers_for_streaming(piece) or "")
        if filtered_piece:
            yield _emit({"type": "content", "content": filtered_piece}, sse_event)

    answer = str(clean_answer_for_frontend("".join(raw_parts)) or "").strip()
    references: list[str] = []
    doi = _extract_doi_from_pdf_filename(pdf_path)
    if doi:
        references.append(doi)

    try:
        log_qa_interaction(
            question=question,
            answer=answer,
            query_mode=cache_key_mode or query_mode,
            references=references[:15],
            extra={
                "pdf_used": True,
                "kb_verification_used": kb_verification is not None,
                "performance_mode": performance_mode,
                "streaming": True,
            },
        )
        cache_set_fn(cache_key_question or question, answer, cache_key_mode or query_mode)
    except Exception:
        pass

    yield _emit(
        {
            "type": "done",
            "references": references,
            "route": "pdf_qa",
        },
        sse_event,
    )
