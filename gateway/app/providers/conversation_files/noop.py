"""No-op conversation file metadata provider."""

from __future__ import annotations

from fastapi import Request

from app.models.files import ConversationFileRow


class NoopConversationFileProvider:
    @property
    def provider_name(self) -> str:
        return "noop"

    async def list_files(
        self,
        *,
        conversation_id: int | str | None,
        request: Request | None = None,
    ) -> list[ConversationFileRow]:
        _ = conversation_id, request
        return []
