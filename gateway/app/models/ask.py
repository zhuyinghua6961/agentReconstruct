"""Gateway-facing ask request models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


_MAX_CHAT_MESSAGE_CHARS = 4000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=_MAX_CHAT_MESSAGE_CHARS)

    @field_validator("content", mode="before")
    @classmethod
    def _truncate_oversized_content(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > _MAX_CHAT_MESSAGE_CHARS:
            return value[:_MAX_CHAT_MESSAGE_CHARS]
        return value


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | str | None = None
    user_id: int | str | None = None
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    requested_mode: Literal["fast", "thinking", "patent"]
    pdf_context: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
