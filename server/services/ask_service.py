"""Ask service adapter: mode routing + run_agent integration + SSE events."""

from __future__ import annotations

import concurrent.futures
import atexit
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Generator

import config
from server.schemas.request_models import AskRequest
from server.services.mode_profiles import RuntimeProfile, get_runtime_profile

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_BRACKET_CITATION_PATTERN = re.compile(r"\[(10\.\d{4,9}/[-._;()/:A-Z0-9]+)(?:,\s*[^\]]+)?\]", re.IGNORECASE)


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
        if "查询分解完成" in raw_message:
            return "阶段1：查询分解完成，开始组织子问题", data
        if "直接回答完成" in raw_message:
            return "阶段1：直接回答完成，进入综合阶段", data
        return "阶段1：开始执行直接回答与查询分解", data

    if stage == "step2":
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

    if stage == "step4":
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
        doi = match.rstrip(".,;)").strip()
        if not doi:
            continue
        key = doi.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(doi)
    return refs


def _build_reference_links(references: list[str]) -> list[dict[str, str]]:
    return [{"doi": doi, "pdf_url": f"/api/v1/view_pdf/{doi}"} for doi in references]


def _adapt_answer_for_frontend(text: str) -> str:
    content = str(text or "")
    if not content:
        return ""

    def _replace(match: re.Match[str]) -> str:
        doi = str(match.group(1) or "").strip()
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
        stream_callback=callbacks.get("stream_callback"),
        step_callback=callbacks.get("step_callback"),
        progress_callback=callbacks.get("progress_callback"),
        enable_thinking=profile.enable_thinking,
        num_sub_questions=profile.num_sub_questions,
        retrieval_top_k=profile.retrieval_top_k,
        max_check_loops=profile.max_check_loops,
        cancel_event=callbacks.get("cancel_event"),
    )


def execute_ask(
    *,
    request: AskRequest,
    timeout_seconds: int,
    trace_id: str,
) -> dict[str, Any]:
    """Execute non-stream ask and return response data payload."""
    profile = resolve_profile(request.mode)
    cancel_event = threading.Event()

    future = _get_agent_executor().submit(
        _run_agent_for_profile,
        request.question,
        profile,
        cancel_event=cancel_event,
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
    return {
        "final_answer": frontend_answer,
        "timings": state.timings,
        "metadata": {
            "mode": profile.mode,
            "query_mode": profile.mode,
            "conversation_id": request.conversation_id,
        },
        "references": references,
        "pdf_links": links,
        "reference_links": links,
        "trace_id": trace_id,
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
    event_queue: queue.Queue[Any] = queue.Queue()
    stop_token = object()
    result_holder: dict[str, Any] = {}
    step_idx = 0
    cancel_event = threading.Event()
    streamed_raw_content = ""
    streamed_adapted_content = ""

    def on_step(description: str, elapsed: float) -> None:
        nonlocal step_idx
        step_idx += 1
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
        event_queue.put(normalized)
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
        nonlocal streamed_raw_content
        streamed_raw_content += str(text or "")
        safe_len = _safe_stream_adapt_prefix_length(streamed_raw_content)
        adapted = _adapt_answer_for_frontend(streamed_raw_content[:safe_len])
        _emit_adapted_delta(adapted)

    def worker() -> None:
        future = _get_agent_executor().submit(
            _run_agent_for_profile,
            request.question,
            profile,
            stream_callback=on_content,
            step_callback=on_step,
            progress_callback=on_progress,
            cancel_event=cancel_event,
        )
        result_holder["future"] = future

    yield {
        "type": "metadata",
        "mode": profile.mode,
        "query_mode": profile.mode,
        "trace_id": trace_id,
        "ts": _utc_iso(),
    }

    started_at = time.monotonic()
    worker()

    while True:
        elapsed = time.monotonic() - started_at
        remaining = max(0.0, float(timeout_seconds) - elapsed)
        if remaining <= 0:
            cancel_event.set()
            future = result_holder.get("future")
            if future is not None:
                cancel = getattr(future, "cancel", None)
                if callable(cancel):
                    cancel()
            yield {
                "type": "error",
                "code": "UPSTREAM_TIMEOUT",
                "error": "upstream timeout",
                "message": "upstream model timeout",
                "retriable": True,
                "trace_id": trace_id,
            }
            return

        future = result_holder.get("future")
        if future is not None and future.done():
            try:
                result_holder["state"] = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                result_holder["error"] = exc
            finally:
                result_holder.pop("future", None)
                event_queue.put(stop_token)

        wait_s = min(float(heartbeat_seconds), remaining)
        try:
            item = event_queue.get(timeout=max(0.05, wait_s))
        except queue.Empty:
            yield {"type": "heartbeat", "trace_id": trace_id}
            continue

        if item is stop_token:
            break
        if isinstance(item, dict):
            yield item

    if streamed_raw_content:
        _emit_adapted_delta(_adapt_answer_for_frontend(streamed_raw_content))

    state = result_holder.get("state")
    if "error" in result_holder:
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
    yield {
        "type": "done",
        "mode": profile.mode,
        "final_answer": frontend_answer,
        "timings": state.timings,
        "references": references,
        "pdf_links": links,
        "reference_links": links,
        "trace_id": trace_id,
    }
