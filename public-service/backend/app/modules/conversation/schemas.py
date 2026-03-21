from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None)


class ConversationMessagePayload(BaseModel):
    role: str = Field(default="")
    content: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AddConversationMessageRequest(BaseModel):
    message: ConversationMessagePayload = Field(default_factory=ConversationMessagePayload)


class UpdateConversationTitleRequest(BaseModel):
    title: str | None = Field(default=None)
