"""Gateway-facing ask request models."""

from __future__ import annotations

import re
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
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    requested_mode: Literal["fast", "thinking", "patent"]
    pdf_context: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_request_id", mode="before")
    @classmethod
    def _normalize_client_request_id(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", text):
            raise ValueError("client_request_id contains invalid characters")
        return text
