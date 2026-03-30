"""Internal quota client for public-service authority calls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from app.core.config import GatewaySettings
from app.core.trace import TRACE_ID_HEADER, get_trace_id


@dataclass(frozen=True)
class QuotaProxyResult:
    status_code: int
    payload: dict[str, Any]

    @property
    def success(self) -> bool:
        return bool(self.payload.get("success"))


class QuotaProxyService:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def precheck(
        self,
        *,
        request: Request,
        user_id: int | None,
        quota_type: str,
        strict_config: bool = False,
    ) -> QuotaProxyResult:
        return await self._post_json(
            request=request,
            path="/internal/quota/grants/precheck",
            payload={
                "user_id": int(user_id or 0),
                "quota_type": str(quota_type or "").strip(),
                "strict_config": bool(strict_config),
            },
        )

    async def finalize(
        self,
        *,
        request: Request,
        grant_id: str,
        success: bool,
    ) -> QuotaProxyResult:
        return await self._post_json(
            request=request,
            path=f"/internal/quota/grants/{str(grant_id or '').strip()}/finalize",
            payload={"success": bool(success)},
        )

    async def _post_json(
        self,
        *,
        request: Request,
        path: str,
        payload: dict[str, Any],
    ) -> QuotaProxyResult:
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
            return QuotaProxyResult(
                status_code=503,
                payload={
                    "success": False,
                    "code": "QUOTA_INTERNAL_UNAVAILABLE",
                    "error": "quota_internal_unavailable",
                    "message": str(exc),
                },
            )
        try:
            data = response.json()
        except Exception:
            data = {
                "success": False,
                "code": "QUOTA_INTERNAL_INVALID_RESPONSE",
                "error": "quota_internal_invalid_response",
                "message": response.text,
            }
        if not isinstance(data, dict):
            data = {
                "success": False,
                "code": "QUOTA_INTERNAL_INVALID_RESPONSE",
                "error": "quota_internal_invalid_response",
                "message": "non_object_response",
            }
        return QuotaProxyResult(status_code=int(response.status_code or 500), payload=data)

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
