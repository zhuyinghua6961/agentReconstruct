from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.conversation.authority_schemas import (
    AuthorityContextHints,
    AuthorityRequestBase,
    AuthorityUserMessagePayload,
)


class AuthorityTaskCreateTurnRequest(AuthorityRequestBase):
    task_id: str = Field(min_length=1)
    message: AuthorityUserMessagePayload
    context_hints: AuthorityContextHints = Field(default_factory=AuthorityContextHints)
    status: Literal["queued", "admitted", "running"]
    last_seq: int = Field(default=0, ge=0)


class AuthorityTaskAssistantStartRequest(AuthorityRequestBase):
    task_id: str = Field(min_length=1)
    status: Literal["queued", "admitted", "running"]
    last_seq: int = Field(default=0, ge=0)


class AuthorityTaskAssistantProgressRequest(BaseModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    task_id: str = Field(min_length=1)
    status: Literal["queued", "admitted", "running"]
    content_delta: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    last_seq: int = Field(ge=0)


class AuthorityTaskAssistantTerminalRequest(BaseModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    task_id: str = Field(min_length=1)
    terminal_status: Literal["completed", "failed", "canceled", "expired"]
    last_seq: int = Field(ge=0)
    answer_text: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    failure: dict[str, Any] = Field(default_factory=dict)


class AuthorityTaskCreateRollbackRequest(BaseModel):
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    task_id: str = Field(min_length=1)
    user_message_id: str = ""
    assistant_message_id: str = ""
    preserve_user_message: bool = False
