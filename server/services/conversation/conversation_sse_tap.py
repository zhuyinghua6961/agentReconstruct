"""SSE stream tap for non-intrusive conversation persistence."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, Iterator


def _extract_reference_doi(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("doi") or "").strip()
    return ""


def _normalize_step_status(value: Any, *, default: str = "processing") -> str:
    text = str(value or "").strip().lower()
    if text in {"processing", "in_progress", "running", "pending"}:
        return "processing"
    if text in {"success", "succeeded", "completed", "complete", "done", "ok"}:
        return "success"
    if text in {"error", "failed", "fail", "failure"}:
        return "error"
    return default


def _build_step_payload(
    payload: Dict[str, Any],
    *,
    fallback_step: str,
    fallback_message: str = "",
    fallback_status: str = "processing",
) -> Dict[str, Any]:
    step_name = str(payload.get("step") or fallback_step).strip() or fallback_step
    message = str(payload.get("message") or payload.get("content") or fallback_message or step_name).strip()
    status = _normalize_step_status(payload.get("status"), default=fallback_status)

    step_payload: Dict[str, Any] = {
        "step": step_name,
        "message": message,
        "status": status,
    }

    data = payload.get("data")
    if data is not None:
        step_payload["data"] = data
    elif "count" in payload:
        step_payload["data"] = {"count": payload.get("count")}

    error_text = str(payload.get("error") or "").strip()
    if error_text:
        step_payload["error"] = error_text
        step_payload["status"] = "error"

    return step_payload


def tap_ask_stream_events(
    *,
    stream_iterable: Iterable[Any],
    on_complete: Callable[[Dict[str, Any]], None],
    logger: Any,
) -> Iterator[Any]:
    """Pass through SSE chunks while collecting assistant result summary."""

    assistant_content_parts: list[str] = []
    query_mode: str = ""
    references: list[str] = []
    steps: list[Dict[str, Any]] = []
    step_positions: dict[str, int] = {}
    thinking_index = 0
    active_step_key = ""
    done_seen = False
    buffer = ""

    def _upsert_step(step_payload: Dict[str, Any]) -> None:
        key = str(step_payload.get("step") or "").strip()
        if not key:
            return
        idx = step_positions.get(key)
        if idx is None:
            step_positions[key] = len(steps)
            steps.append(dict(step_payload))
            return
        merged = dict(steps[idx])
        merged.update({k: v for k, v in step_payload.items() if v is not None and v != ""})
        if not str(merged.get("message") or "").strip():
            merged["message"] = str(steps[idx].get("message") or key)
        merged["status"] = _normalize_step_status(merged.get("status"))
        steps[idx] = merged

    def _mark_step_status(step_key: str, status: str) -> None:
        idx = step_positions.get(step_key)
        if idx is None:
            return
        steps[idx]["status"] = _normalize_step_status(status)

    def _handle_event_payload(payload: Dict[str, Any]) -> None:
        nonlocal query_mode, references, done_seen, thinking_index, active_step_key
        event_type = str(payload.get("type") or "")
        if event_type == "content":
            assistant_content_parts.append(str(payload.get("content") or ""))
        elif event_type == "thinking":
            message = str(payload.get("content") or payload.get("message") or "").strip()
            if message:
                if active_step_key:
                    _mark_step_status(active_step_key, "success")
                thinking_index += 1
                step_payload = _build_step_payload(
                    {"step": f"thinking_{thinking_index}", "message": message, "status": "processing"},
                    fallback_step=f"thinking_{thinking_index}",
                )
                _upsert_step(step_payload)
                active_step_key = step_payload["step"]
        elif event_type == "step":
            step_payload = _build_step_payload(payload, fallback_step=f"step_{len(steps) + 1}")
            _upsert_step(step_payload)
            active_step_key = str(step_payload.get("step") or active_step_key)
        elif event_type == "metadata":
            query_mode = str(payload.get("query_mode") or payload.get("queryMode") or query_mode)
        elif event_type == "error":
            error_text = str(payload.get("error") or payload.get("message") or "").strip()
            if active_step_key and active_step_key in step_positions:
                idx = step_positions[active_step_key]
                steps[idx]["status"] = "error"
                if error_text:
                    steps[idx]["error"] = error_text
                    if not str(steps[idx].get("message") or "").strip():
                        steps[idx]["message"] = error_text
            elif error_text:
                _upsert_step(
                    _build_step_payload(
                        {
                            "step": "error",
                            "message": error_text,
                            "status": "error",
                            "error": error_text,
                        },
                        fallback_step="error",
                    )
                )
        elif event_type == "done":
            done_seen = True
            if active_step_key:
                _mark_step_status(active_step_key, "success")
            for item in steps:
                if str(item.get("status") or "") == "processing":
                    item["status"] = "success"
            refs = payload.get("references")
            if isinstance(refs, list):
                references[:] = [doi for doi in (_extract_reference_doi(item) for item in refs) if doi]

    try:
        for chunk in stream_iterable:
            text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            buffer += text

            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                frame = frame.strip()
                if not frame.startswith("data: "):
                    continue
                body = frame[6:].strip()
                if not body:
                    continue
                try:
                    payload = json.loads(body)
                    if isinstance(payload, dict):
                        _handle_event_payload(payload)
                except Exception:
                    continue

            yield chunk
    finally:
        try:
            summary = {
                "assistant_content": "".join(assistant_content_parts).strip(),
                "query_mode": query_mode,
                "references": references,
                "steps": [dict(item) for item in steps],
                "done_seen": done_seen,
            }
            on_complete(summary)
        except Exception as exc:  # pragma: no cover - hook safety
            logger.warning("conversation SSE tap completion hook failed: %s", exc)
