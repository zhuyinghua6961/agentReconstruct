"""Conversation file metadata service backed by a pluggable provider."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.models.files import ConversationFileRow
from app.providers.conversation_files.base import ConversationFileProvider
from app.providers.conversation_files.noop import NoopConversationFileProvider
from app.services.conversation_file_normalizer import normalize_conversation_file_rows


class ConversationFileService:
    def __init__(self, *, provider: ConversationFileProvider | None = None) -> None:
        self._provider: ConversationFileProvider = provider or NoopConversationFileProvider()

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "provider_name", "unknown") or "unknown")

    def set_provider(self, provider: ConversationFileProvider) -> None:
        self._provider = provider

    def set_transport(self, transport: Any) -> None:
        setter = getattr(self._provider, "set_transport", None)
        if callable(setter):
            setter(transport)

    async def list_files(
        self,
        *,
        conversation_id: int | str | None,
        request: Request | None = None,
    ) -> list[ConversationFileRow]:
        return list(await self._provider.list_files(conversation_id=conversation_id, request=request) or [])

    def normalize_rows(self, rows: list[dict[str, Any]] | None) -> list[ConversationFileRow]:
        return normalize_conversation_file_rows(rows)
