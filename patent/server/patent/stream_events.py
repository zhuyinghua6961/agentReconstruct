from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


PATENT_STREAM_CAPABILITY_HEADER = "x-patent-stream-capability"
PATENT_STREAM_CAPABILITY_OPTION = "patent_stream_capability"
PATENT_STREAM_CAPABILITY_PREVIEW_V1 = "preview_v1"

_FILE_ROUTES = frozenset({"pdf_qa", "tabular_qa", "hybrid_qa"})
_PREVIEW_ROUTES = frozenset({"hybrid_qa"})
_CONTENT_ROLES = frozenset({"preview", "final"})
_CONTENT_SOURCES = frozenset({"pdf", "table", "kb", "hybrid"})
_CONTENT_PHASES = frozenset({"start", "delta", "end", "snapshot"})


def normalize_stream_capability(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if text == PATENT_STREAM_CAPABILITY_PREVIEW_V1:
        return PATENT_STREAM_CAPABILITY_PREVIEW_V1
    return None


def inject_stream_capability_option(
    options: Mapping[str, Any] | None,
    *,
    header_value: str | None,
    route: str | None,
) -> dict[str, Any]:
    normalized_options = dict(options or {})
    normalized_options.pop(PATENT_STREAM_CAPABILITY_OPTION, None)
    capability = normalize_stream_capability(header_value)
    if capability is None or str(route or "") not in _FILE_ROUTES:
        return normalized_options
    normalized_options[PATENT_STREAM_CAPABILITY_OPTION] = capability
    return normalized_options


def structured_content_streaming_enabled(*, options: Mapping[str, Any] | None, route: str | None) -> bool:
    return (
        str(route or "") in _FILE_ROUTES
        and normalize_stream_capability(str((options or {}).get(PATENT_STREAM_CAPABILITY_OPTION) or "")) == PATENT_STREAM_CAPABILITY_PREVIEW_V1
    )


def preview_streaming_enabled(*, options: Mapping[str, Any] | None, route: str | None, source_scope: str | None) -> bool:
    del source_scope
    return str(route or "") in _PREVIEW_ROUTES and structured_content_streaming_enabled(options=options, route=route)


def final_content_source_for_route(route: str | None) -> str:
    normalized_route = str(route or "").strip()
    if normalized_route == "pdf_qa":
        return "pdf"
    if normalized_route == "tabular_qa":
        return "table"
    return "hybrid"


def normalize_content_event_fields(
    *,
    content_role: str | None = None,
    content_source: str | None = None,
    content_stream_id: str | None = None,
    content_phase: str | None = None,
    replace_stream: bool | None = None,
) -> dict[str, Any]:
    role = _normalize_literal(content_role, allowed=_CONTENT_ROLES, field_name="content_role")
    source = _normalize_literal(content_source, allowed=_CONTENT_SOURCES, field_name="content_source")
    stream_id = _normalize_stream_id(content_stream_id)
    phase = _normalize_literal(content_phase, allowed=_CONTENT_PHASES, field_name="content_phase")
    replace = _normalize_replace_stream(replace_stream)

    has_structured_fields = any(
        value is not None
        for value in (content_role, content_source, content_stream_id, content_phase, replace_stream)
    )
    if not has_structured_fields:
        return {
            "content_role": None,
            "content_source": None,
            "content_stream_id": None,
            "content_phase": None,
            "replace_stream": None,
        }

    if role is None:
        raise ValueError("content_role is required for structured content events")
    if source is None:
        raise ValueError("content_source is required for structured content events")

    if role == "preview":
        if stream_id is None:
            raise ValueError("preview content requires content_stream_id")
        if phase is None:
            raise ValueError("preview content requires content_phase")

    if role == "final":
        if stream_id is not None and stream_id != "final:answer":
            raise ValueError("final content_stream_id must be final:answer")
        if phase is None:
            phase = "snapshot"

    if replace is not None and phase not in {"start", "snapshot"}:
        raise ValueError("replace_stream is only allowed for start or snapshot content phases")

    return {
        "content_role": role,
        "content_source": source,
        "content_stream_id": stream_id,
        "content_phase": phase,
        "replace_stream": replace,
    }


@dataclass
class PatentFinalContentStreamEmitter:
    callback: Any
    content_source: str
    _stream_started: bool = False

    def __call__(self, chunk: Any) -> None:
        if not callable(self.callback):
            return
        text = str(chunk or "")
        if not text:
            return
        payload = {
            "content": text,
            "content_role": "final",
            "content_source": self.content_source,
            "content_stream_id": "final:answer",
            "content_phase": "start" if not self._stream_started else "delta",
        }
        if not self._stream_started:
            payload["replace_stream"] = True
            self._stream_started = True
        self.callback(payload)

    def emit_snapshot(self, content: Any) -> None:
        if not callable(self.callback):
            return
        text = str(content or "")
        if not text:
            return
        self.callback(
            {
                "content": text,
                "content_role": "final",
                "content_source": self.content_source,
                "content_stream_id": "final:answer",
                "content_phase": "snapshot",
                "replace_stream": True,
            }
        )
        self._stream_started = False

    def close(self) -> None:
        if not self._stream_started or not callable(self.callback):
            return
        self.callback(
            {
                "content": "",
                "content_role": "final",
                "content_source": self.content_source,
                "content_stream_id": "final:answer",
                "content_phase": "end",
            }
        )
        self._stream_started = False

    def abort(self) -> None:
        self._stream_started = False


@dataclass
class PatentPreviewContentStreamEmitter:
    callback: Any
    content_source: str
    content_stream_id: str
    _stream_started: bool = False

    def __call__(self, chunk: Any) -> None:
        if not callable(self.callback):
            return
        text = str(chunk or "")
        if not text:
            return
        payload = {
            "content": text,
            "content_role": "preview",
            "content_source": self.content_source,
            "content_stream_id": self.content_stream_id,
            "content_phase": "start" if not self._stream_started else "delta",
        }
        if not self._stream_started:
            payload["replace_stream"] = True
            self._stream_started = True
        self.callback(payload)

    def emit_snapshot(self, content: Any) -> None:
        if not callable(self.callback):
            return
        text = str(content or "")
        if not text:
            return
        self.callback(
            {
                "content": text,
                "content_role": "preview",
                "content_source": self.content_source,
                "content_stream_id": self.content_stream_id,
                "content_phase": "snapshot",
                "replace_stream": True,
            }
        )
        self._stream_started = False

    def close(self) -> None:
        if not self._stream_started or not callable(self.callback):
            return
        self.callback(
            {
                "content": "",
                "content_role": "preview",
                "content_source": self.content_source,
                "content_stream_id": self.content_stream_id,
                "content_phase": "end",
            }
        )
        self._stream_started = False

    def abort(self) -> None:
        self._stream_started = False


@dataclass
class PatentContentStreamState:
    _open_streams: set[str] = field(default_factory=set)
    _final_started: bool = False

    def observe(self, event: Mapping[str, Any]) -> None:
        if str(event.get("type") or "") != "content":
            return

        normalized = normalize_content_event_fields(
            content_role=event.get("content_role"),
            content_source=event.get("content_source"),
            content_stream_id=event.get("content_stream_id"),
            content_phase=event.get("content_phase"),
            replace_stream=event.get("replace_stream"),
        )
        role = normalized["content_role"]
        if role is None:
            return

        phase = str(normalized["content_phase"])
        stream_id = normalized["content_stream_id"] or ("final:answer" if role == "final" else None)
        if role == "preview" and self._final_started:
            raise ValueError("preview content is not allowed after final content starts")
        if role == "final":
            has_open_preview_streams = any(open_stream_id != "final:answer" for open_stream_id in self._open_streams)
            if not self._final_started and has_open_preview_streams:
                raise ValueError("all preview streams must close before final content starts")
            self._final_started = True

        if phase == "snapshot":
            if stream_id is not None:
                self._open_streams.discard(stream_id)
            return

        if stream_id is None:
            raise ValueError("content stream phases require a content_stream_id")

        if phase == "start":
            if stream_id in self._open_streams:
                raise ValueError("start content requires a closed content stream")
            self._open_streams.add(stream_id)
            return

        if phase == "delta":
            if stream_id not in self._open_streams:
                raise ValueError("delta content requires a prior start event")
            return

        if phase == "end":
            if stream_id not in self._open_streams:
                raise ValueError("end content requires a prior start event")
            self._open_streams.remove(stream_id)


@dataclass
class PatentStructuredContentRouter:
    callback: Any
    state: PatentContentStreamState = field(default_factory=PatentContentStreamState)

    def __call__(self, payload: Mapping[str, Any] | dict[str, Any]) -> None:
        self.emit(payload)

    def emit(self, payload: Mapping[str, Any] | dict[str, Any]) -> None:
        if not callable(self.callback):
            return
        normalized_payload = dict(payload or {})
        normalized_payload.setdefault("type", "content")
        self.state.observe(normalized_payload)
        self.callback(normalized_payload)

    def final_emitter(self, *, content_source: str) -> PatentFinalContentStreamEmitter:
        return PatentFinalContentStreamEmitter(
            callback=self.emit,
            content_source=content_source,
        )

    def preview_emitter(self, *, content_source: str, content_stream_id: str) -> PatentPreviewContentStreamEmitter:
        return PatentPreviewContentStreamEmitter(
            callback=self.emit,
            content_source=content_source,
            content_stream_id=content_stream_id,
        )

    def emit_final_snapshot(self, *, content: Any, content_source: str) -> None:
        if not callable(self.callback):
            return
        text = str(content or "")
        if not text:
            return
        self.emit(
            {
                "content": text,
                "content_role": "final",
                "content_source": content_source,
                "content_stream_id": "final:answer",
                "content_phase": "snapshot",
                "replace_stream": True,
            }
        )


def _normalize_literal(value: str | None, *, allowed: frozenset[str], field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    if text not in allowed:
        joined = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of {{{joined}}}")
    return text


def _normalize_stream_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("content_stream_id must be a string")
    text = value.strip()
    if not text:
        raise ValueError("content_stream_id must be a non-empty string")
    return text


def _normalize_replace_stream(value: bool | None) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("replace_stream must be boolean")
    return value
