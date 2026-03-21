"""HTTP-backed conversation file provider via the public backend."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Request

from app.core.trace import TRACE_ID_HEADER, get_trace_id
from app.models.files import ConversationFileRow
from app.providers.conversation_files.base import ConversationFileProviderError
from app.services.conversation_file_normalizer import normalize_conversation_file_rows


class PublicHttpConversationFileProvider:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._transport = transport

    @property
    def provider_name(self) -> str:
        return "public_http"

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def list_files(
        self,
        *,
        conversation_id: int | str | None,
        request: Request | None = None,
    ) -> list[ConversationFileRow]:
        if conversation_id in (None, ""):
            return []

        url = f"{self._base_url}/api/conversations/{conversation_id}/files"
        headers = self._build_headers(request)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers=headers)
        except Exception as exc:
            raise ConversationFileProviderError(
                f"conversation_file_provider_request_failed:{exc}",
                provider=self.provider_name,
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise ConversationFileProviderError(
                f"conversation_file_provider_http_{response.status_code}",
                provider=self.provider_name,
                status_code=503,
            )

        try:
            payload: Any = response.json()
        except Exception as exc:
            raise ConversationFileProviderError(
                "conversation_file_provider_invalid_json",
                provider=self.provider_name,
            ) from exc
        rows = self._extract_rows(payload)
        return normalize_conversation_file_rows(rows)

    def _build_headers(self, request: Request | None) -> dict[str, str]:
        if request is None:
            return {"Accept": "application/json"}
        headers: dict[str, str] = {"Accept": "application/json"}
        auth = str(request.headers.get("authorization") or "").strip()
        if auth:
            headers["Authorization"] = auth
        trace_id = get_trace_id(request)
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id
        return headers

    def _extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("files"), list):
                return data.get("files") or []
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            if isinstance(payload.get("files"), list):
                return [row for row in payload.get("files") or [] if isinstance(row, dict)]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []
