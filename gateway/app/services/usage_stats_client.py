"""Internal usage-stats client for public-service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from app.core.config import GatewaySettings
from app.core.trace import TRACE_ID_HEADER, get_trace_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageStatsClientResult:
    status_code: int
    payload: dict[str, Any]

    @property
    def success(self) -> bool:
        return bool(self.payload.get("success"))


class UsageStatsClient:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def record_event(
        self,
        *,
        request: Request,
        user_id: int,
        event_type: str,
        trace_id: str | None = None,
        conversation_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UsageStatsClientResult:
        if int(user_id) <= 0:
            return UsageStatsClientResult(status_code=400, payload={"success": False, "code": "VALIDATION_ERROR"})
        return await self._post_json(
            request=request,
            path="/internal/activity/record",
            payload={
                "user_id": int(user_id),
                "event_type": str(event_type or "").strip(),
                "trace_id": str(trace_id or "").strip() or None,
                "conversation_id": int(conversation_id) if conversation_id else None,
                "metadata": metadata if isinstance(metadata, dict) else None,
            },
        )

    async def _post_json(
        self,
        *,
        request: Request,
        path: str,
        payload: dict[str, Any],
    ) -> UsageStatsClientResult:
        url = f"{self._settings.endpoints.public}{path}"
        headers = self._build_headers(request)
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.request_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except Exception as exc:
            logger.warning("usage_stats internal record unavailable: %s", exc)
            return UsageStatsClientResult(
                status_code=503,
                payload={
                    "success": False,
                    "code": "USAGE_STATS_INTERNAL_UNAVAILABLE",
                    "error": "usage_stats_internal_unavailable",
                },
            )
        try:
            data = response.json()
        except Exception:
            data = {"success": False, "code": "USAGE_STATS_INTERNAL_INVALID_RESPONSE"}
        if not isinstance(data, dict):
            data = {"success": False, "code": "USAGE_STATS_INTERNAL_INVALID_RESPONSE"}
        if not data.get("success"):
            logger.warning(
                "usage_stats internal record failed: status=%s code=%s error=%s",
                response.status_code,
                data.get("code"),
                data.get("error"),
            )
        return UsageStatsClientResult(status_code=int(response.status_code or 500), payload=data)

    def _build_headers(self, request: Request) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Internal-Service-Name": "gateway",
            "X-Internal-Service-Token": self._internal_token(),
        }
        trace_id = get_trace_id(request)
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id
        return headers

    def _internal_token(self) -> str:
        token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
        if token:
            return token
        if str(self._settings.environment or "").strip().lower() == "test":
            return "authority-test-token"
        return ""
