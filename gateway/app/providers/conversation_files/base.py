"""Base protocol for conversation file metadata providers."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from app.models.files import ConversationFileRow


class ConversationFileProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = int(status_code)


class ConversationFileProvider(Protocol):
    async def list_files(
        self,
        *,
        conversation_id: int | str | None,
        request: Request | None = None,
    ) -> list[ConversationFileRow]:
        ...

    @property
    def provider_name(self) -> str:
        ...
