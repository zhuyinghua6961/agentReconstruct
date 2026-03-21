"""Gateway-facing ask request models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | str | None = None
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    requested_mode: Literal["fast", "thinking", "patent"] = "fast"
    pdf_context: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["fast", "thinking", "patent"] | None = None
