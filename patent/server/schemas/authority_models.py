from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from server.schemas.request_models import PatentRouteName, PatentSourceScope


class _StrictAuthorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorityContextHints(_StrictAuthorityModel):
    selected_file_ids: list[int] = Field(default_factory=list)
    last_turn_route_hint: str | None = None
    mode_origin_requested_mode: str | None = None
    mode_origin_execution_backend: str | None = None
    compatibility_route: bool | None = None


class AuthorityMessage(_StrictAuthorityModel):
    role: Literal["user"]
    content: str


class AuthorityUserWriteRequest(_StrictAuthorityModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    trace_id: str
    source_service: Literal["patentQA"] = "patentQA"
    route: PatentRouteName = "kb_qa"
    source_scope: PatentSourceScope = "kb"
    requested_mode: Literal["patent"] = "patent"
    actual_mode: Literal["patent"] = "patent"
    idempotency_key: str
    message: AuthorityMessage
    context_hints: AuthorityContextHints = Field(default_factory=AuthorityContextHints)


class AuthorityContextSnapshotQuery(_StrictAuthorityModel):
    user_id: int = Field(gt=0)
    trace_id: str
    source_service: Literal["patentQA"] = "patentQA"
    route: PatentRouteName = "kb_qa"
    source_scope: PatentSourceScope = "kb"
    requested_mode: Literal["patent"] = "patent"
    actual_mode: Literal["patent"] = "patent"


class AuthorityContextSnapshotResponse(_StrictAuthorityModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    snapshot_version: int
    updated_at: str
    summary: dict[str, Any]
    recent_turns: list[dict[str, Any]]
    conversation_state: dict[str, Any]


class AuthorityAssistantFinalEvent(_StrictAuthorityModel):
    done_seen: Literal[True] = True
    answer_text: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)
    reference_objects: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[dict[str, Any]] = Field(default_factory=list)
    original_links: list[dict[str, Any]] = Field(default_factory=list)
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)


class AuthorityAssistantAsyncRequest(_StrictAuthorityModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    trace_id: str
    source_service: Literal["patentQA"] = "patentQA"
    route: PatentRouteName = "kb_qa"
    source_scope: PatentSourceScope = "kb"
    requested_mode: Literal["patent"] = "patent"
    actual_mode: Literal["patent"] = "patent"
    idempotency_key: str
    final_event: AuthorityAssistantFinalEvent
