"""Ask service adapter: mode routing + run_agent integration + SSE events."""

from __future__ import annotations

import concurrent.futures
import atexit
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Generator

import config
from server.schemas.request_models import AskRequest
from server.services.conversation_context_service import ConversationContext, build_conversation_context
from server.services.mode_profiles import RuntimeProfile, get_runtime_profile
from server.services.query_rewrite_service import QuestionRewriteResult, rewrite_question
from server.storage.paper_storage import normalize_doi

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_BRACKET_CITATION_PATTERN = re.compile(r"\[(10\.\d{4,9}/[-._;()/:A-Z0-9]+)(?:,\s*[^\]]+)?\]", re.IGNORECASE)
logger = logging.getLogger(__name__)


class AskServiceError(Exception):
    pass


class ModeNotSupportedError(AskServiceError):
    pass


class ModeNotImplementedError(AskServiceError):
    pass


class AskTimeoutError(AskServiceError):
    pass


_AGENT_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _get_agent_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _AGENT_EXECUTOR
    if _AGENT_EXECUTOR is not None:
        return _AGENT_EXECUTOR
    _AGENT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, int(config.ASK_EXECUTOR_MAX_WORKERS)),
        thread_name_prefix="ask-agent",
    )
    return _AGENT_EXECUTOR


def _shutdown_agent_executor() -> None:
    global _AGENT_EXECUTOR
    if _AGENT_EXECUTOR is None:
        return
    try:
        _AGENT_EXECUTOR.shutdown(wait=False, cancel_futures=False)
    except Exception:
        pass
    _AGENT_EXECUTOR = None


atexit.register(_shutdown_agent_executor)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chunk_text(text: str, *, chunk_size: int = 700) -> list[str]:
    content = str(text or "")
    if not content:
        return []
    return [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]


def _short_text(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _bool_attr(value: Any, *names: str) -> bool:
    for name in names:
        try:
            attr = getattr(value, name)
        except Exception:
            continue
        if attr is None:
            continue
        return bool(attr)
    return False


def _text_attr(value: Any, *names: str) -> str:
    for name in names:
        try:
            attr = getattr(value, name)
        except Exception:
            continue
        if attr is None:
            continue
        text = str(attr).strip()
        if text:
            return text
    return ""


def _log_conversation_context_ready(*, trace_id: str, context: ConversationContext) -> None:
    summary_payload = context.summary if isinstance(context.summary, dict) else {}
    summary_keys = sorted(str(key) for key in summary_payload.keys())[:10]
    logger.info(
        "[trace_id=%s] conversation_context ready conversation_id=%s user_id=%s recent_turns=%s summary_available=%s summary_keys=%s raw_question=%s",
        trace_id,
        context.conversation_id,
        context.user_id,
        len(context.recent_turns),
        bool(summary_payload),
        summary_keys,
        _short_text(context.raw_question),
    )


def _log_question_rewrite_ready(*, trace_id: str, rewrite: Any) -> None:
    rewrite_applied = _bool_attr(rewrite, "rewrite_applied", "rewritten")
    summary_used = _bool_attr(rewrite, "summary_used")
    logger.info(
        "[trace_id=%s] question_rewrite ready rewrite_applied=%s summary_used=%s reason=%s effective_question=%s",
        trace_id,
        rewrite_applied,
        summary_used,
        _text_attr(rewrite, "rewrite_reason", "reason") or "unknown",
        _short_text(_text_attr(rewrite, "effective_question") or ""),
    )


def _log_runtime_resource_snapshot(*, trace_id: str, mode: str, route: str | None = None) -> None:
    papers_dir = str(config.PAPERS_DIR or "")
    chroma_dir = str(config.CHROMA_PERSIST_DIR or "")
    collection_name = str(config.CHROMA_COLLECTION_NAME or "")
    papers_exists = os.path.isdir(papers_dir)
    chroma_exists = os.path.isdir(chroma_dir)
    chroma_sqlite = os.path.join(chroma_dir, "chroma.sqlite3") if chroma_dir else ""
    chroma_sqlite_exists = bool(chroma_sqlite) and os.path.exists(chroma_sqlite)
    collection_count: int | None = None
    collection_error = ""
    try:
        from ingest.vector_store import get_collection_count, get_or_create_collection

        collection = get_or_create_collection()
        collection_count = int(get_collection_count(collection))
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        collection_error = f"{type(exc).__name__}: {exc}"
    logger.info(
        "[trace_id=%s] runtime_resource_snapshot mode=%s route=%s papers_dir=%s papers_exists=%s chroma_persist_dir=%s chroma_exists=%s chroma_sqlite=%s sqlite_exists=%s chroma_collection=%s collection_count=%s collection_error=%s",
        trace_id,
        mode,
        str(route or ""),
        papers_dir,
        papers_exists,
        chroma_dir,
        chroma_exists,
        chroma_sqlite,
        chroma_sqlite_exists,
        collection_name,
        collection_count if collection_count is not None else "unknown",
        collection_error or "",
    )


def _normalize_step_status(raw: str, *, default: str = "processing") -> str:
    value = str(raw or "").strip().lower()
    if value in {"processing", "in_progress", "running", "pending", "started"}:
        return "processing"
    if value in {"success", "succeeded", "completed", "complete", "done", "ok"}:
        return "success"
    if value in {"error", "failed", "fail", "failure"}:
        return "error"
    return default


def _format_frontend_step_message(stage: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_message = str(payload.get("message") or stage or "处理中").strip() or "处理中"
    data = dict(payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}

    if stage == "step1":
        if "等待直接回答" in raw_message:
            return "阶段1：子问题处理完成，等待直接回答收尾", data
        if "查询分解完成" in raw_message:
            return "阶段1：查询分解完成，开始组织子问题", data
        if "直接回答完成" in raw_message:
            return "阶段1：直接回答完成，进入综合阶段", data
        return "阶段1：开始执行直接回答与查询分解", data

    if stage == "step2":
        if "全部完成" in raw_message:
            completed = data.get("completed")
            try:
                completed_int = int(completed)
            except Exception:
                completed_int = 0
            if completed_int > 0:
                data["count"] = completed_int
            return "阶段2：子问题预回答全部完成，开始等待检索结果", data
        completed = data.get("completed")
        total = data.get("total")
        try:
            completed_int = int(completed)
        except Exception:
            completed_int = 0
        try:
            total_int = int(total)
        except Exception:
            total_int = 0
        if completed_int > 0 and total_int > 0:
            data["count"] = completed_int
            return f"阶段2：子问题预回答：已完成 {completed_int}/{total_int}", data
        return "阶段2：开始执行子问题预回答与检索流水线", data

    if stage == "step3":
        submitted_batches = data.get("submitted_batches")
        completed_batches = data.get("completed_batches")
        total_batches = data.get("total_batches")
        retrieved_chunks_total = data.get("retrieved_chunks_total")
        try:
            submitted_batches_int = int(submitted_batches)
        except Exception:
            submitted_batches_int = 0
        try:
            completed_batches_int = int(completed_batches)
        except Exception:
            completed_batches_int = 0
        try:
            total_batches_int = int(total_batches)
        except Exception:
            total_batches_int = 0
        try:
            retrieved_chunks_total_int = int(retrieved_chunks_total)
        except Exception:
            retrieved_chunks_total_int = 0

        if completed_batches_int > 0 and total_batches_int > 0:
            data["count"] = completed_batches_int
            if "检索完成" in raw_message:
                return f"阶段3：文献检索完成，共获得 {retrieved_chunks_total_int} 个文段", data
            return f"阶段3：文献检索：已完成 {completed_batches_int}/{total_batches_int} 批", data
        if submitted_batches_int > 0 and total_batches_int > 0:
            data["count"] = submitted_batches_int
            return f"阶段3：已提交 {submitted_batches_int}/{total_batches_int} 批检索任务", data
        return "阶段3：开始文献检索", data

    if stage == "step4":
        if "开始流式输出" in raw_message:
            return "阶段4：综合草稿开始流式输出", data
        return "阶段4：开始综合生成草稿答案", data

    if stage == "step5_check":
        loop_no = data.get("check_loop")
        issue_count = data.get("issues")
        try:
            loop_int = int(loop_no)
        except Exception:
            loop_int = 0
        try:
            issue_count_int = int(issue_count)
        except Exception:
            issue_count_int = 0
        if loop_int > 0:
            data["count"] = loop_int
        if "跳过" in raw_message:
            return "阶段5A：当前配置跳过引用检查", data
        if "引用检查通过" in raw_message:
            return raw_message.replace("第 ", "阶段5A：第 ", 1), data
        if "发现" in raw_message and "引用问题" in raw_message:
            if loop_int > 0:
                return f"阶段5A：第 {loop_int} 轮发现 {issue_count_int} 个引用问题", data
            return raw_message, data
        if "开始第 " in raw_message:
            return raw_message.replace("开始第 ", "阶段5A：开始第 ", 1), data
        return "阶段5A：开始引用检查", data

    if stage == "step5_revise":
        loop_no = data.get("check_loop")
        issue_count = data.get("issues")
        try:
            loop_int = int(loop_no)
        except Exception:
            loop_int = 0
        try:
            issue_count_int = int(issue_count)
        except Exception:
            issue_count_int = 0
        if loop_int > 0:
            data["count"] = loop_int
        if "修订完成" in raw_message:
            if loop_int > 0:
                return f"阶段5B：第 {loop_int} 轮问题修订完成", data
            return "阶段5B：问题修订完成", data
        if "开始第 " in raw_message:
            if loop_int > 0 and issue_count_int > 0:
                return f"阶段5B：开始第 {loop_int} 轮问题修订（{issue_count_int} 个问题）", data
            return raw_message.replace("开始第 ", "阶段5B：开始第 ", 1), data
        return "阶段5B：开始问题修订", data

    if stage == "step5":
        loop_no = data.get("check_loop")
        try:
            loop_int = int(loop_no)
        except Exception:
            loop_int = 0
        if loop_int > 0:
            data["count"] = loop_int
        if "引用检查通过" in raw_message:
            return raw_message.replace("第 ", "阶段5：第 ", 1), data
        if "开始第 " in raw_message:
            return raw_message.replace("开始第 ", "阶段5：开始第 ", 1), data
        if "发现" in raw_message and "引用问题" in raw_message:
            return raw_message.replace("第 ", "阶段5：第 ", 1), data
        return "阶段5：开始引用验证与必要修订", data

    return raw_message, data


def _progress_to_step_event(payload: dict[str, Any]) -> dict[str, Any]:
    stage = str(payload.get("stage") or "").strip() or "progress"
    status = _normalize_step_status(str(payload.get("status") or ""), default="processing")
    message, data = _format_frontend_step_message(stage, payload)
    event = {
        "type": "step",
        "step": stage,
        "message": message,
        "status": status,
        "data": data,
    }
    if payload.get("error"):
        event["error"] = str(payload["error"])
    return event


def _extract_references(text: str) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    for match in _DOI_PATTERN.findall(str(text or "")):
        doi = normalize_doi(match.rstrip(".,;)").strip())
        if not doi:
            continue
        key = doi.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(doi)
    return refs


def _build_reference_links(references: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in references:
        doi = normalize_doi(raw)
        if not doi:
            continue
        key = doi.lower()
        if key in seen:
            continue
        seen.add(key)
        links.append({"doi": doi, "pdf_url": f"/api/v1/view_pdf/{doi}"})
    return links


def _build_done_metadata(
    *,
    profile: RuntimeProfile,
    request: AskRequest,
    context: ConversationContext,
    rewrite: QuestionRewriteResult,
) -> dict[str, Any]:
    return {
        "mode": profile.mode,
        "requested_mode": request.requested_mode,
        "actual_mode": request.actual_mode,
        "route": request.route,
        "turn_mode": request.turn_mode,
        "query_mode": profile.mode,
        "conversation_id": request.conversation_id,
        "raw_question": context.raw_question,
        "effective_question": rewrite.effective_question,
        "rewrite_applied": rewrite.rewrite_applied,
        "rewrite_reason": rewrite.rewrite_reason,
        "context_turns": len(context.recent_turns),
        "summary_available": bool(context.summary),
        "summary_updated_at": str(context.summary.get("updated_at") or "") if isinstance(context.summary, dict) else "",
    }


def _adapt_answer_for_frontend(text: str) -> str:
    content = str(text or "")
    if not content:
        return ""

    def _replace(match: re.Match[str]) -> str:
        doi = normalize_doi(str(match.group(1) or "").strip())
        if not doi:
            return match.group(0)
        return f"[DOI: {doi}]"

    return _BRACKET_CITATION_PATTERN.sub(_replace, content)


def _safe_stream_adapt_prefix_length(text: str) -> int:
    content = str(text or "")
    if not content:
        return 0
    last_open = content.rfind("[")
    last_close = content.rfind("]")
    if last_open > last_close:
        return last_open
    return len(content)


def _build_preflight_step_events(
    *,
    context: ConversationContext,
    rewrite: QuestionRewriteResult,
) -> list[dict[str, Any]]:
    context_message = f"已完成上下文整理（最近 {len(context.recent_turns)} 条消息）"
    if bool(context.summary):
        context_message += "并加载会话摘要"
    rewrite_message = "已完成问题改写" if rewrite.rewrite_applied else "当前问题无需改写"
    return [
        {
            "type": "step",
            "step": "context_ready",
            "message": context_message,
            "status": "success",
            "data": {
                "context_turns": len(context.recent_turns),
                "summary_available": bool(context.summary),
            },
        },
        {
            "type": "step",
            "step": "rewrite_ready",
            "message": rewrite_message,
            "status": "success",
            "data": {
                "rewrite_applied": bool(rewrite.rewrite_applied),
                "rewrite_reason": str(rewrite.rewrite_reason or ""),
            },
        },
    ]


def resolve_profile(mode: str) -> RuntimeProfile:
    try:
        profile = get_runtime_profile(mode)
    except KeyError as exc:
        raise ModeNotSupportedError(f"unsupported mode: {mode}") from exc
    if not profile.implemented:
        raise ModeNotImplementedError(f"mode not implemented yet: {mode}")
    return profile


def _run_agent_for_profile(question: str, profile: RuntimeProfile, **callbacks: Any):
    from agent_core.graph import run_agent

    return run_agent(
        question=question,
        raw_question=callbacks.get("raw_question"),
        conversation_context=callbacks.get("conversation_context"),
        stream_callback=callbacks.get("stream_callback"),
        step_callback=callbacks.get("step_callback"),
        progress_callback=callbacks.get("progress_callback"),
        enable_thinking=profile.enable_thinking,
        num_sub_questions=profile.num_sub_questions,
        retrieval_top_k=profile.retrieval_top_k,
        max_check_loops=profile.max_check_loops,
        cancel_event=callbacks.get("cancel_event"),
        trace_id=callbacks.get("trace_id"),
    )


def _prepare_execution(request: AskRequest) -> tuple[ConversationContext, QuestionRewriteResult]:
    context = build_conversation_context(request=request)
    try:
        rewrite = rewrite_question(
            raw_question=context.raw_question,
            recent_turns=context.recent_turns,
            summary=context.summary,
        )
    except Exception:
        rewrite = QuestionRewriteResult(
            raw_question=context.raw_question,
            effective_question=context.raw_question,
            rewrite_applied=False,
            rewrite_reason="rewrite_failed",
        )
    return context, rewrite


def execute_ask(
    *,
    request: AskRequest,
    timeout_seconds: int,
    trace_id: str,
) -> dict[str, Any]:
    """Execute non-stream ask and return response data payload."""
    profile = resolve_profile(request.mode)
    context, rewrite = _prepare_execution(request)
    cancel_event = threading.Event()
    logger.info(
        "[trace_id=%s] execute_ask start mode=%s conversation_id=%s question=%s",
        trace_id,
        profile.mode,
        request.conversation_id,
        str(context.raw_question or "")[:120],
    )
    _log_conversation_context_ready(trace_id=trace_id, context=context)
    _log_question_rewrite_ready(trace_id=trace_id, rewrite=rewrite)
    _log_runtime_resource_snapshot(trace_id=trace_id, mode=profile.mode, route=request.route)

    future = _get_agent_executor().submit(
        _run_agent_for_profile,
        rewrite.effective_question,
        profile,
        raw_question=context.raw_question,
        conversation_context={
            "recent_turns": list(context.recent_turns),
            "summary": dict(context.summary),
            "conversation_id": context.conversation_id,
            "user_id": context.user_id,
        },
        cancel_event=cancel_event,
        trace_id=trace_id,
    )
    try:
        state = future.result(timeout=max(1, int(timeout_seconds)))
    except concurrent.futures.TimeoutError as exc:
        cancel_event.set()
        cancel = getattr(future, "cancel", None)
        if callable(cancel):
            cancel()
        raise AskTimeoutError("upstream model timeout") from exc

    if getattr(state, "error", ""):
        if str(getattr(state, "error", "")).strip().lower() == "cancelled":
            raise AskTimeoutError("upstream model timeout")
        raise AskServiceError(str(state.error))

    frontend_answer = _adapt_answer_for_frontend(state.final_answer)
    references = _extract_references(state.final_answer)
    links = _build_reference_links(references)
    logger.info(
        "[trace_id=%s] execute_ask done answer_chars=%s references=%s timings=%s",
        trace_id,
        len(frontend_answer),
        len(references),
        dict(getattr(state, "timings", {}) or {}),
    )
    return {
        "final_answer": frontend_answer,
        "timings": state.timings,
        "metadata": _build_done_metadata(profile=profile, request=request, context=context, rewrite=rewrite),
        "references": references,
        "pdf_links": links,
        "reference_links": links,
        "trace_id": trace_id,
        "used_files": list(request.used_files or []),
    }


def stream_ask_events(
    *,
    request: AskRequest,
    timeout_seconds: int,
    heartbeat_seconds: int,
    trace_id: str,
) -> Generator[dict[str, Any], None, None]:
    """Yield structured ask-stream events (not encoded as SSE yet)."""
    profile = resolve_profile(request.mode)
    context, rewrite = _prepare_execution(request)
    logger.info(
        "[trace_id=%s] stream_ask_events start mode=%s conversation_id=%s question=%s",
        trace_id,
        profile.mode,
        request.conversation_id,
        str(context.raw_question or "")[:120],
    )
    _log_conversation_context_ready(trace_id=trace_id, context=context)
    _log_question_rewrite_ready(trace_id=trace_id, rewrite=rewrite)
    _log_runtime_resource_snapshot(trace_id=trace_id, mode=profile.mode, route=request.route)
    event_queue: queue.Queue[Any] = queue.Queue()
    stop_token = object()
    result_holder: dict[str, Any] = {}
    step_idx = 0
    cancel_event = threading.Event()
    streamed_raw_content = ""
    streamed_adapted_content = ""
    first_content_logged = False

    def on_step(description: str, elapsed: float) -> None:
        nonlocal step_idx
        step_idx += 1
        logger.info(
            "[trace_id=%s] stream step callback step=%s elapsed=%.3fs message=%s",
            trace_id,
            step_idx,
            float(elapsed),
            str(description),
        )
        event_queue.put(
            {
                "type": "step",
                "step": f"step{step_idx}",
                "message": str(description),
                "status": "success",
                "data": {"elapsed_seconds": round(float(elapsed), 3)},
            }
        )

    def on_progress(payload: dict[str, Any]) -> None:
        normalized = dict(payload)
        logger.info(
            "[trace_id=%s] stream progress stage=%s status=%s message=%s data=%s",
            trace_id,
            normalized.get("stage"),
            normalized.get("status"),
            normalized.get("message"),
            normalized.get("data"),
        )
        event_queue.put(_progress_to_step_event(normalized))

    def _emit_adapted_delta(adapted_text: str) -> None:
        nonlocal streamed_adapted_content
        content = str(adapted_text or "")
        if not content:
            return
        if content.startswith(streamed_adapted_content):
            delta = content[len(streamed_adapted_content):]
        else:
            delta = content
        if not delta:
            streamed_adapted_content = content
            return
        streamed_adapted_content = content
        for part in _chunk_text(delta):
            event_queue.put({"type": "content", "content": part})

    def on_content(text: str) -> None:
        nonlocal streamed_raw_content, first_content_logged
        streamed_raw_content += str(text or "")
        if not first_content_logged and text:
            first_content_logged = True
            logger.info(
                "[trace_id=%s] stream first raw content chunk chars=%s",
                trace_id,
                len(str(text or "")),
            )
        safe_len = _safe_stream_adapt_prefix_length(streamed_raw_content)
        adapted = _adapt_answer_for_frontend(streamed_raw_content[:safe_len])
        _emit_adapted_delta(adapted)

    def worker() -> None:
        logger.info("[trace_id=%s] stream worker submit to ask executor", trace_id)
        future = _get_agent_executor().submit(
            _run_agent_for_profile,
            rewrite.effective_question,
            profile,
            raw_question=context.raw_question,
            conversation_context={
                "recent_turns": list(context.recent_turns),
                "summary": dict(context.summary),
                "conversation_id": context.conversation_id,
                "user_id": context.user_id,
            },
            stream_callback=on_content,
            step_callback=on_step,
            progress_callback=on_progress,
            cancel_event=cancel_event,
            trace_id=trace_id,
        )
        result_holder["future"] = future

    yield {
        "type": "metadata",
        "mode": profile.mode,
        "requested_mode": request.requested_mode,
        "actual_mode": request.actual_mode,
        "route": request.route,
        "turn_mode": request.turn_mode,
        "query_mode": profile.mode,
        "trace_id": trace_id,
        "raw_question": context.raw_question,
        "effective_question": rewrite.effective_question,
        "rewrite_applied": rewrite.rewrite_applied,
        "rewrite_reason": rewrite.rewrite_reason,
        "context_turns": len(context.recent_turns),
        "summary_available": bool(context.summary),
        "summary_updated_at": str(context.summary.get("updated_at") or "") if isinstance(context.summary, dict) else "",
        "ts": _utc_iso(),
    }
    for event in _build_preflight_step_events(context=context, rewrite=rewrite):
        yield event

    started_at = time.monotonic()
    worker()

    while True:
        elapsed = time.monotonic() - started_at
        remaining = max(0.0, float(timeout_seconds) - elapsed)
        if remaining <= 0:
            logger.warning("[trace_id=%s] stream timeout after %.3fs", trace_id, elapsed)
            cancel_event.set()
            future = result_holder.get("future")
            if future is not None:
                cancel = getattr(future, "cancel", None)
                if callable(cancel):
                    cancel()
            yield {
                "type": "error",
                "code": "UPSTREAM_TIMEOUT",
                "error": "upstream_timeout",
                "message": "upstream model timeout",
                "retriable": True,
                "trace_id": trace_id,
            }
            return

        future = result_holder.get("future")
        if future is not None and future.done():
            try:
                logger.info("[trace_id=%s] stream future completed, collecting state", trace_id)
                result_holder["state"] = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("[trace_id=%s] stream future raised: %s", trace_id, exc)
                result_holder["error"] = exc
            finally:
                result_holder.pop("future", None)
                event_queue.put(stop_token)

        wait_s = min(float(heartbeat_seconds), remaining)
        try:
            item = event_queue.get(timeout=max(0.05, wait_s))
        except queue.Empty:
            yield {
                "type": "heartbeat",
                "trace_id": trace_id,
            }
            continue

        if item is stop_token:
            break
        if isinstance(item, dict):
            yield item

    if streamed_raw_content:
        _emit_adapted_delta(_adapt_answer_for_frontend(streamed_raw_content))

    state = result_holder.get("state")
    if "error" in result_holder:
        logger.error("[trace_id=%s] stream finished with upstream error=%s", trace_id, result_holder["error"])
        yield {
            "type": "error",
            "code": "UPSTREAM_ERROR",
            "error": "upstream_error",
            "message": str(result_holder["error"]),
            "retriable": True,
            "trace_id": trace_id,
        }
        return

    if state is None:
        logger.error("[trace_id=%s] stream finished without state", trace_id)
        yield {
            "type": "error",
            "code": "INTERNAL_ERROR",
            "error": "internal_error",
            "message": "empty execution result",
            "retriable": False,
            "trace_id": trace_id,
        }
        return

    if getattr(state, "error", ""):
        logger.error("[trace_id=%s] stream state error=%s", trace_id, state.error)
        if str(getattr(state, "error", "")).strip().lower() == "cancelled":
            yield {
                "type": "error",
                "code": "UPSTREAM_TIMEOUT",
                "error": "upstream_timeout",
                "message": "upstream model timeout",
                "retriable": True,
                "trace_id": trace_id,
            }
            return
        yield {
            "type": "error",
            "code": "UPSTREAM_ERROR",
            "error": "upstream_error",
            "message": str(state.error),
            "retriable": True,
            "trace_id": trace_id,
        }
        return

    frontend_answer = _adapt_answer_for_frontend(state.final_answer)
    references = _extract_references(state.final_answer)
    links = _build_reference_links(references)
    logger.info(
        "[trace_id=%s] stream done total_chars=%s references=%s",
        trace_id,
        len(frontend_answer),
        len(references),
    )
    yield {
        "type": "done",
        "mode": profile.mode,
        "requested_mode": request.requested_mode,
        "actual_mode": request.actual_mode,
        "route": request.route,
        "turn_mode": request.turn_mode,
        "final_answer": frontend_answer,
        "timings": state.timings,
        "references": references,
        "pdf_links": links,
        "reference_links": links,
        "doi_locations": [],
        "trace_id": trace_id,
        "used_files": list(request.used_files or []),
        "metadata": _build_done_metadata(profile=profile, request=request, context=context, rewrite=rewrite),
        "file_selection": {},
    }
