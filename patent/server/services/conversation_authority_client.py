from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from config import get_settings
from server.schemas.authority_models import (
    AuthorityAssistantAsyncRequest,
    AuthorityAssistantTerminalAsyncRequest,
    AuthorityContextSnapshotQuery,
    AuthorityContextSnapshotResponse,
    AuthorityUserWriteRequest,
)


class AuthorityFeatureDisabledError(RuntimeError):
    """Raised when durable authority flow is not enabled for patent."""


class _UserWriteResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    conversation_id: int = Field(gt=0)
    message_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    deduped: bool


class _AssistantAsyncResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accepted: bool
    event_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: str = Field(min_length=1)


class ConversationAuthorityClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = str(base_url or settings.authority.base_url).rstrip("/")
        self._service_token = str(service_token or settings.authority.internal_token).strip()
        self._durable_enabled = bool(settings.authority.durable_enabled)
        resolved_timeout = timeout if timeout is not None else float(settings.authority.timeout_seconds)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=resolved_timeout,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def write_user_turn(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        source_scope: str = "kb",
        content: str,
        selected_file_ids: list[int] | None = None,
        last_turn_route_hint: str | None = None,
        mode_origin_requested_mode: str | None = None,
        mode_origin_execution_backend: str | None = None,
        compatibility_route: bool | None = None,
    ) -> dict[str, Any]:
        self._ensure_durable_authority_enabled()
        payload = AuthorityUserWriteRequest(
            conversation_id=int(conversation_id),
            user_id=int(user_id),
            trace_id=str(trace_id),
            route=str(route),
            source_scope=str(source_scope),
            requested_mode=str(requested_mode),
            actual_mode=str(actual_mode),
            idempotency_key=self._idempotency_key(conversation_id=conversation_id, trace_id=trace_id, operation="user"),
            message={"role": "user", "content": str(content)},
            context_hints={
                "selected_file_ids": list(selected_file_ids or []),
                "last_turn_route_hint": str(last_turn_route_hint or "").strip() or None,
                "mode_origin_requested_mode": str(mode_origin_requested_mode or "").strip() or None,
                "mode_origin_execution_backend": str(mode_origin_execution_backend or "").strip() or None,
                "compatibility_route": compatibility_route if isinstance(compatibility_route, bool) else None,
            },
        )
        response = self._request(
            method="POST",
            path=f"/internal/conversations/{int(conversation_id)}/messages/user",
            trace_id=trace_id,
            json=payload.model_dump(exclude_none=True),
        )
        return _UserWriteResponse.model_validate(response).model_dump()

    def read_context_snapshot(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        source_scope: str = "kb",
    ) -> dict[str, Any]:
        self._ensure_durable_authority_enabled()
        query = AuthorityContextSnapshotQuery(
            user_id=int(user_id),
            trace_id=str(trace_id),
            route=str(route),
            source_scope=str(source_scope),
            requested_mode=str(requested_mode),
            actual_mode=str(actual_mode),
        )
        response = self._request(
            method="GET",
            path=f"/internal/conversations/{int(conversation_id)}/context-snapshot",
            trace_id=trace_id,
            params=query.model_dump(exclude_none=True),
        )
        return AuthorityContextSnapshotResponse.model_validate(response).model_dump()

    def accept_assistant_turn_async(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        source_scope: str = "kb",
        answer_text: str,
        metadata: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
        reference_objects: list[dict[str, Any]] | None = None,
        reference_links: list[dict[str, Any]] | None = None,
        original_links: list[dict[str, Any]] | None = None,
        used_files: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
        runtime_owner_token: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_durable_authority_enabled()
        payload = AuthorityAssistantAsyncRequest(
            conversation_id=int(conversation_id),
            user_id=int(user_id),
            trace_id=str(trace_id),
            route=str(route),
            source_scope=str(source_scope),
            requested_mode=str(requested_mode),
            actual_mode=str(actual_mode),
            idempotency_key=self._idempotency_key(conversation_id=conversation_id, trace_id=trace_id, operation="assistant"),
            runtime_owner_token=str(runtime_owner_token or "").strip() or None,
            final_event={
                "done_seen": True,
                "answer_text": str(answer_text),
                "steps": list(steps or []),
                "metadata": dict(metadata or {}),
                "references": list(references or []),
                "reference_objects": list(reference_objects or []),
                "reference_links": list(reference_links or []),
                "original_links": list(original_links or []),
                "used_files": list(used_files or []),
                "timings": dict(timings or {}),
            },
        )
        response = self._request(
            method="POST",
            path=f"/internal/conversations/{int(conversation_id)}/messages/assistant-async",
            trace_id=trace_id,
            json=payload.model_dump(exclude_none=True),
        )
        return _AssistantAsyncResponse.model_validate(response).model_dump()

    def accept_assistant_terminal_async(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        source_scope: str = "kb",
        terminal_status: str,
        answer_text: str = "",
        metadata: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
        reference_objects: list[dict[str, Any]] | None = None,
        reference_links: list[dict[str, Any]] | None = None,
        original_links: list[dict[str, Any]] | None = None,
        used_files: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
        runtime_owner_token: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_durable_authority_enabled()
        payload = AuthorityAssistantTerminalAsyncRequest(
            conversation_id=int(conversation_id),
            user_id=int(user_id),
            trace_id=str(trace_id),
            route=str(route),
            source_scope=str(source_scope),
            requested_mode=str(requested_mode),
            actual_mode=str(actual_mode),
            idempotency_key=self._idempotency_key(conversation_id=conversation_id, trace_id=trace_id, operation="assistant"),
            runtime_owner_token=str(runtime_owner_token or "").strip() or None,
            terminal_event={
                "terminal_status": str(terminal_status),
                "done_seen": str(terminal_status).strip().lower() == "done",
                "answer_text": str(answer_text or ""),
                "steps": list(steps or []),
                "metadata": dict(metadata or {}),
                "references": list(references or []),
                "reference_objects": list(reference_objects or []),
                "reference_links": list(reference_links or []),
                "original_links": list(original_links or []),
                "used_files": list(used_files or []),
                "timings": dict(timings or {}),
                "failure": dict(failure or {}) or None,
            },
        )
        response = self._request(
            method="POST",
            path=f"/internal/conversations/{int(conversation_id)}/messages/assistant-terminal-async",
            trace_id=trace_id,
            json=payload.model_dump(exclude_none=True),
        )
        return _AssistantAsyncResponse.model_validate(response).model_dump()

    def _ensure_durable_authority_enabled(self) -> None:
        if not self._durable_enabled:
            raise AuthorityFeatureDisabledError("durable patent authority flow is disabled")

    def _request(
        self,
        *,
        method: str,
        path: str,
        trace_id: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.request(
            method=method,
            url=f"{self._base_url}{path}",
            headers=self._headers(trace_id=trace_id),
            json=json,
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("authority response must be a JSON object")
        return payload

    def _headers(self, *, trace_id: str) -> dict[str, str]:
        return {
            "X-Internal-Service-Name": "patentQA",
            "X-Internal-Service-Token": self._service_token,
            "X-Trace-Id": str(trace_id),
        }

    @staticmethod
    def _idempotency_key(*, conversation_id: int, trace_id: str, operation: str) -> str:
        return f"{int(conversation_id)}:{str(trace_id)}:{str(operation)}"
