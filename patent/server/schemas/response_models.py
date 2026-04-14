from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from server.patent.stream_events import normalize_content_event_fields
from server.schemas.request_models import PatentRouteName, PatentSourceScope


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_CANONICAL_PATENT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]+$")


def _validate_references(value: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            raise ValueError("references items must be non-empty strings")
        if text != text.upper() or _CANONICAL_PATENT_ID_RE.fullmatch(text) is None:
            raise ValueError("references items must be canonical patent identifiers")
        normalized.append(text)
    return normalized


class PatentSyncSuccess(_StrictModel):
    success: Literal[True] = True
    final_answer: str
    query_mode: str
    route: PatentRouteName
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    source_scope: PatentSourceScope
    timings: dict[str, Any]
    references: list[str] = Field(default_factory=list)
    reference_objects: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[dict[str, Any]] = Field(default_factory=list)
    original_links: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    file_selection: dict[str, Any] = Field(default_factory=dict)

    @field_validator("references")
    @classmethod
    def validate_references(cls, value: list[str]) -> list[str]:
        return _validate_references(value)


class _BaseEvent(_StrictModel):
    seq: int = Field(ge=0)
    ts: str


class MetadataEvent(_BaseEvent):
    type: Literal["metadata"] = "metadata"
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    route: PatentRouteName
    query_mode: str
    source_scope: PatentSourceScope
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class ContentEvent(_BaseEvent):
    type: Literal["content"] = "content"
    content: str
    content_role: Literal["preview", "final"] | None = None
    content_source: Literal["pdf", "table", "kb", "hybrid"] | None = None
    content_stream_id: str | None = None
    content_phase: Literal["start", "delta", "end", "snapshot"] | None = None
    replace_stream: bool | None = None

    @model_validator(mode="after")
    def validate_content_contract(self) -> "ContentEvent":
        normalized = normalize_content_event_fields(
            content_role=self.content_role,
            content_source=self.content_source,
            content_stream_id=self.content_stream_id,
            content_phase=self.content_phase,
            replace_stream=self.replace_stream,
        )
        self.content_role = normalized["content_role"]
        self.content_source = normalized["content_source"]
        self.content_stream_id = normalized["content_stream_id"]
        self.content_phase = normalized["content_phase"]
        self.replace_stream = normalized["replace_stream"]
        return self


class StepEvent(_BaseEvent):
    type: Literal["step"] = "step"
    step: str | None = None
    title: str | None = None
    message: str | None = None
    detail: str | None = None
    status: str | None = None
    error: str | None = None
    data: dict[str, Any] | None = None


class HeartbeatEvent(_BaseEvent):
    type: Literal["heartbeat"] = "heartbeat"


class DoneEvent(_BaseEvent):
    type: Literal["done"] = "done"
    final_answer: str
    query_mode: str
    route: PatentRouteName
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    source_scope: PatentSourceScope
    timings: dict[str, Any]
    references: list[str] = Field(default_factory=list)
    reference_objects: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[dict[str, Any]] = Field(default_factory=list)
    original_links: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    file_selection: dict[str, Any] = Field(default_factory=dict)

    @field_validator("references")
    @classmethod
    def validate_references(cls, value: list[str]) -> list[str]:
        return _validate_references(value)


class ErrorEvent(_BaseEvent):
    type: Literal["error"] = "error"
    code: str
    error: str
    message: str
    trace_id: str
