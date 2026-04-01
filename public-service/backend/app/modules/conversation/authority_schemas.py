from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AuthorityRequestBase(BaseModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    trace_id: str = Field(min_length=1)
    source_service: Literal["fastQA", "highThinkingQA"]
    route: str = Field(min_length=1)
    requested_mode: Literal["fast", "thinking"]
    actual_mode: Literal["fast", "thinking"]


class AuthorityUserMessagePayload(BaseModel):
    role: Literal["user"]
    content: str = Field(min_length=1)


class AuthorityContextHints(BaseModel):
    selected_file_ids: list[int] = Field(default_factory=list)
    last_turn_route_hint: str | None = None


class AuthorityUserWriteRequest(AuthorityRequestBase):
    idempotency_key: str = Field(min_length=1)
    message: AuthorityUserMessagePayload
    context_hints: AuthorityContextHints = Field(default_factory=AuthorityContextHints)


class AuthorityConversationSummary(BaseModel):
    short_summary: str = Field(default="", description="Minimal authority-generated summary from final user/assistant turns only.")
    memory_facts: list[str] = Field(default_factory=list, description="Stable facts distilled from recent assistant turns for downstream context reuse.")
    open_threads: list[str] = Field(default_factory=list, description="Latest unresolved user threads that remain open for the next turn.")


class AuthorityRecentTurn(BaseModel):
    message_id: str
    role: Literal["user", "assistant"] = Field(description="Only final conversation roles are exposed by authority snapshots.")
    content: str
    created_at: datetime
    trace_id: str = Field(default="", description="Trace identifier for the final turn; execution traces themselves are excluded.")
    status: Literal["done", "failed", "canceled"] = "done"
    terminal_status: Literal["done", "failed", "canceled"] = "done"
    failure_stage: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    retriable: bool | None = None


class AuthorityConversationState(BaseModel):
    last_turn_route: str | None = Field(default=None, description="Last assistant route selected by the QA authority flow.")
    last_focus_file_ids: list[int] = Field(default_factory=list, description="File identifiers derived from the last assistant turn's used files.")
    last_assistant_trace_id: str | None = Field(default=None, description="Trace identifier of the last assistant final turn.")


class AuthorityContextSnapshotRequest(AuthorityRequestBase):
    pass


class AuthorityContextSnapshotResponse(BaseModel):
    conversation_id: int
    user_id: int
    snapshot_version: int
    updated_at: datetime
    summary: AuthorityConversationSummary = Field(default_factory=AuthorityConversationSummary)
    recent_turns: list[AuthorityRecentTurn] = Field(default_factory=list)
    conversation_state: AuthorityConversationState = Field(default_factory=AuthorityConversationState)


class AuthorityAssistantFinalEvent(BaseModel):
    done_seen: bool
    answer_text: str = Field(min_length=1)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    reference_objects: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[dict[str, Any]] = Field(default_factory=list)
    pdf_links: list[dict[str, Any]] = Field(default_factory=list)
    doi_locations: dict[str, Any] = Field(default_factory=dict)
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_done_seen(self) -> "AuthorityAssistantFinalEvent":
        if self.done_seen is not True:
            raise ValueError("final_event must represent a completed assistant turn")
        return self


class AuthorityAssistantAsyncRequest(AuthorityRequestBase):
    idempotency_key: str = Field(min_length=1)
    final_event: AuthorityAssistantFinalEvent


class AuthorityAssistantTerminalFailure(BaseModel):
    stage: str | None = None
    message: str | None = None
    code: str | None = None
    retriable: bool | None = None


class AuthorityAssistantTerminalEvent(BaseModel):
    terminal_status: Literal["done", "failed", "canceled"]
    done_seen: bool
    answer_text: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    reference_objects: list[dict[str, Any]] = Field(default_factory=list)
    reference_links: list[dict[str, Any]] = Field(default_factory=list)
    pdf_links: list[dict[str, Any]] = Field(default_factory=list)
    doi_locations: dict[str, Any] = Field(default_factory=dict)
    used_files: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)
    failure: AuthorityAssistantTerminalFailure | None = None

    @model_validator(mode="after")
    def validate_terminal_contract(self) -> "AuthorityAssistantTerminalEvent":
        status = str(self.terminal_status or "").strip().lower()
        answer_text = str(self.answer_text or "").strip()
        failure = self.failure
        if status == "done":
            if self.done_seen is not True:
                raise ValueError("done terminal event must represent a completed assistant turn")
            if not answer_text:
                raise ValueError("done terminal event requires non-empty answer_text")
            if failure is not None:
                raise ValueError("done terminal event must not include failure metadata")
            return self
        if self.done_seen is not False:
            raise ValueError("failed/canceled terminal event must not mark done_seen")
        if status == "failed":
            if failure is None:
                raise ValueError("failed terminal event requires failure metadata")
            if not str(failure.message or "").strip():
                raise ValueError("failed terminal event requires failure.message")
            if failure.retriable is None:
                raise ValueError("failed terminal event requires failure.retriable")
            return self
        if failure is not None:
            if not str(failure.message or "").strip():
                raise ValueError("canceled terminal event requires failure.message when failure is provided")
            if failure.retriable is not False:
                raise ValueError("canceled terminal event requires failure.retriable=False when provided")
        return self


class AuthorityAssistantTerminalAsyncRequest(AuthorityRequestBase):
    idempotency_key: str = Field(min_length=1)
    terminal_event: AuthorityAssistantTerminalEvent
