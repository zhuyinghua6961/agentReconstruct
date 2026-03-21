from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


def _normalize_step_status(value: Any, default: str = "processing") -> str:
    raw = str(value or "").strip().lower()
    if raw in {"processing", "in_progress", "running", "pending"}:
        return "processing"
    if raw in {"success", "succeeded", "completed", "complete", "done", "ok"}:
        return "success"
    if raw in {"error", "failed", "fail", "failure"}:
        return "error"
    return default


def _clean_step_title(value: str) -> str:
    cleaned = str(value or "").strip().rstrip(" .。…")
    return cleaned or "处理中"


def _split_step_message(raw_message: Any) -> tuple[str, str]:
    message = str(raw_message or "").strip()
    if not message:
        return "处理中", ""
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff#]+", "", message).strip()
    compact = re.sub(r"\s+", " ", cleaned)
    matched = re.match(r"^(阶段[0-9一二三四五六七八九十百千万点.]+)(?:\s*[：:]\s*|\s+)(.+)$", compact)
    if matched:
        return _clean_step_title(matched.group(1)), matched.group(2).strip()
    parts = re.split(r"[：:]", compact, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return _clean_step_title(parts[0]), parts[1].strip()
    if len(compact) <= 18:
        return _clean_step_title(compact), ""
    return _clean_step_title(compact[:18]), compact


def _derive_step_key(*, payload: dict[str, Any], title: str, detail: str) -> str:
    explicit = str(payload.get("step") or "").strip()
    if explicit:
        return explicit
    base = title or detail or str(payload.get("message") or payload.get("content") or "step")
    compact = re.sub(r"\s+", "", str(base or "").strip())
    compact = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", compact).strip("_")
    return compact or "step"


def normalize_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event or {})
    event_type = str(payload.get("type") or "").strip().lower()
    if event_type not in {"thinking", "step"}:
        return payload

    message = str(payload.get("message") or payload.get("content") or "").strip()
    title, detail = _split_step_message(message)
    normalized = dict(payload)
    normalized["type"] = "step"
    normalized["step"] = _derive_step_key(payload=payload, title=title, detail=detail)
    normalized["message"] = message or normalized["step"]
    normalized["title"] = str(payload.get("title") or title or normalized["step"])
    normalized["detail"] = str(payload.get("detail") or detail or "")
    normalized["status"] = _normalize_step_status(payload.get("status"), "processing")
    return normalized


@dataclass
class AskStreamSummary:
    assistant_content: str = ""
    query_mode: str = ""
    references: list[Any] = field(default_factory=list)
    reference_objects: list[Any] = field(default_factory=list)
    steps: list[Any] = field(default_factory=list)
    route: str = ""
    used_files: list[Any] = field(default_factory=list)
    timings: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    file_selection: dict[str, Any] = field(default_factory=dict)
    done_seen: bool = False


class AskStreamTap:
    def __init__(self) -> None:
        self.summary = AskStreamSummary()

    def _upsert_summary_step(self, payload: dict[str, Any]) -> None:
        step_key = str(payload.get("step") or "").strip()
        if not step_key:
            self.summary.steps.append(payload)
            return
        for idx, existing in enumerate(self.summary.steps):
            if str((existing or {}).get("step") or "").strip() == step_key:
                merged = dict(existing or {})
                merged.update(payload)
                self.summary.steps[idx] = merged
                return
        self.summary.steps.append(payload)

    def wrap(self, source: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        for item in source:
            payload = normalize_stream_event(dict(item or {}))
            event_type = str(payload.get("type") or "")
            if event_type == "content":
                self.summary.assistant_content += str(payload.get("content") or "")
            elif event_type == "metadata":
                self.summary.query_mode = str(payload.get("query_mode") or self.summary.query_mode)
            elif event_type == "step":
                self._upsert_summary_step(payload)
            elif event_type == "done":
                self.summary.done_seen = True
                refs = payload.get("references")
                self.summary.references = refs if isinstance(refs, list) else self.summary.references
                reference_objects = payload.get("reference_objects")
                if isinstance(reference_objects, list):
                    self.summary.reference_objects = reference_objects
                elif isinstance(refs, list) and refs and isinstance(refs[0], dict):
                    self.summary.reference_objects = refs
                self.summary.route = str(payload.get("route") or self.summary.route)
                used_files = payload.get("used_files")
                if isinstance(used_files, list):
                    self.summary.used_files = used_files
                timings = payload.get("timings")
                if isinstance(timings, dict):
                    self.summary.timings = timings
                self.summary.trace_id = str(payload.get("trace_id") or self.summary.trace_id)
                file_selection = payload.get("file_selection")
                if isinstance(file_selection, dict):
                    self.summary.file_selection = file_selection
            yield payload
