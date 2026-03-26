from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatentResponseMetadata(_StrictModel):
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    route: Literal["kb_qa"]
    mode: Literal["patent"]
    query_mode: Literal["patent"]
    conversation_id: int | None = None


class PatentSyncData(_StrictModel):
    final_answer: str
    timings: dict[str, Any]
    metadata: PatentResponseMetadata
    references: list[dict[str, Any]] = Field(default_factory=list)
    pdf_links: list[str] = Field(default_factory=list)
    reference_links: list[str] = Field(default_factory=list)
    trace_id: str


class PatentSyncSuccess(_StrictModel):
    success: Literal[True] = True
    data: PatentSyncData
    trace_id: str


class _BaseEvent(_StrictModel):
    seq: int = Field(ge=0)
    ts: str


class MetadataEvent(_BaseEvent):
    type: Literal["metadata"] = "metadata"
    requested_mode: Literal["patent"]
    actual_mode: Literal["patent"]
    route: Literal["kb_qa"]
    query_mode: Literal["patent"]
    trace_id: str


class ContentEvent(_BaseEvent):
    type: Literal["content"] = "content"
    content: str


class StepEvent(_BaseEvent):
    type: Literal["step"] = "step"
    title: str | None = None
    message: str | None = None


class HeartbeatEvent(_BaseEvent):
    type: Literal["heartbeat"] = "heartbeat"


class DoneEvent(_BaseEvent):
    type: Literal["done"] = "done"
    final_answer: str
    timings: dict[str, Any]
    references: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[str] = Field(default_factory=list)
    pdf_links: list[str] = Field(default_factory=list)
    file_selection: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(_BaseEvent):
    type: Literal["error"] = "error"
    code: str
    error: str
    message: str
    trace_id: str
