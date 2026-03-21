"""Static in-memory provider used for tests and local development."""

from __future__ import annotations

from fastapi import Request

from app.models.files import ConversationFileRow


class StaticConversationFileProvider:
    def __init__(self, rows: list[ConversationFileRow] | None = None) -> None:
        self._rows = list(rows or [])

    @property
    def provider_name(self) -> str:
        return "static"

    async def list_files(
        self,
        *,
        conversation_id: int | str | None,
        request: Request | None = None,
    ) -> list[ConversationFileRow]:
        _ = conversation_id, request
        return list(self._rows)
