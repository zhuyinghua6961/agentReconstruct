from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field


_DEFAULT_BASE_URL = "http://127.0.0.1:8102"
_DEFAULT_TIMEOUT_SEC = 10.0
_BASE_URL_ENV = "PUBLIC_SERVICE_INTERNAL_BASE_URL"
_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"
_TIMEOUT_ENV = "PUBLIC_SERVICE_INTERNAL_TIMEOUT_SEC"
_SERVICE_NAME = "fastQA"


class _UserWriteResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    conversation_id: int = Field(gt=0)
    message_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    deduped: bool


class _SnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    short_summary: str = ""
    memory_facts: list[dict[str, Any]] = Field(default_factory=list)
    open_threads: list[dict[str, Any]] = Field(default_factory=list)


class _RecentTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    content: str
    created_at: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)


class _ConversationState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    last_turn_route: str | None = None
    last_focus_file_ids: list[int] = Field(default_factory=list)
    last_assistant_trace_id: str | None = None


class _ContextSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    snapshot_version: int = Field(ge=0)
    updated_at: str = Field(min_length=1)
    summary: _SnapshotSummary = Field(default_factory=_SnapshotSummary)
    recent_turns: list[_RecentTurn] = Field(default_factory=list)
    conversation_state: _ConversationState = Field(default_factory=_ConversationState)


class _AssistantAsyncResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accepted: bool
    event_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: str = Field(min_length=1)


def _normalize_positive_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


class ConversationAuthorityClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        service_name: str = _SERVICE_NAME,
        transport: httpx.BaseTransport | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = str(base_url or os.getenv(_BASE_URL_ENV, _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL).rstrip("/")
        self._service_token = str(service_token or os.getenv(_TOKEN_ENV, "") or "").strip()
        self._service_name = str(service_name or _SERVICE_NAME).strip() or _SERVICE_NAME
        resolved_timeout = timeout
        if resolved_timeout is None:
            try:
                resolved_timeout = float(str(os.getenv(_TIMEOUT_ENV, _DEFAULT_TIMEOUT_SEC) or _DEFAULT_TIMEOUT_SEC))
            except Exception:
                resolved_timeout = _DEFAULT_TIMEOUT_SEC
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
        content: str,
        selected_file_ids: list[int] | None = None,
        last_turn_route_hint: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "conversation_id": int(conversation_id),
            "user_id": int(user_id),
            "trace_id": str(trace_id),
            "source_service": self._service_name,
            "route": str(route),
            "requested_mode": str(requested_mode),
            "actual_mode": str(actual_mode),
            "idempotency_key": self._idempotency_key(conversation_id=conversation_id, trace_id=trace_id, operation="user"),
            "message": {
                "role": "user",
                "content": str(content),
            },
            "context_hints": {
                "selected_file_ids": _normalize_positive_int_list(selected_file_ids),
                "last_turn_route_hint": str(last_turn_route_hint or "").strip() or None,
            },
        }
        response = self._request(
            method="POST",
            path=f"/internal/conversations/{int(conversation_id)}/messages/user",
            trace_id=trace_id,
            json=payload,
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
    ) -> dict[str, Any]:
        response = self._request(
            method="GET",
            path=f"/internal/conversations/{int(conversation_id)}/context-snapshot",
            trace_id=trace_id,
            params={
                "user_id": int(user_id),
                "trace_id": str(trace_id),
                "source_service": self._service_name,
                "route": str(route),
                "requested_mode": str(requested_mode),
                "actual_mode": str(actual_mode),
            },
        )
        return _ContextSnapshotResponse.model_validate(response).model_dump()

    def accept_assistant_turn_async(
        self,
        *,
        user_id: int,
        conversation_id: int,
        trace_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        answer_text: str,
        steps: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
        used_files: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "conversation_id": int(conversation_id),
            "user_id": int(user_id),
            "trace_id": str(trace_id),
            "source_service": self._service_name,
            "route": str(route),
            "requested_mode": str(requested_mode),
            "actual_mode": str(actual_mode),
            "idempotency_key": self._idempotency_key(conversation_id=conversation_id, trace_id=trace_id, operation="assistant"),
            "final_event": {
                "done_seen": True,
                "answer_text": str(answer_text),
                "steps": list(steps or []),
                "references": list(references or []),
                "used_files": list(used_files or []),
                "timings": dict(timings or {}),
            },
        }
        response = self._request(
            method="POST",
            path=f"/internal/conversations/{int(conversation_id)}/messages/assistant-async",
            trace_id=trace_id,
            json=payload,
        )
        return _AssistantAsyncResponse.model_validate(response).model_dump()

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
            "X-Internal-Service-Name": self._service_name,
            "X-Internal-Service-Token": self._service_token,
            "X-Trace-Id": str(trace_id),
        }

    @staticmethod
    def _idempotency_key(*, conversation_id: int, trace_id: str, operation: str) -> str:
        return f"{int(conversation_id)}:{str(trace_id)}:{str(operation)}"
