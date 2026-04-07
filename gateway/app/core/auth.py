"""Gateway auth helpers backed by the public auth API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.core.config import GatewaySettings
from app.core.trace import TRACE_ID_HEADER, get_trace_id


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    role: str = "user"
    username: str = ""


class GatewayAuthService:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def require_auth_context(self, request: Request) -> AuthContext:
        authorization = str(request.headers.get("authorization") or "").strip()
        if not authorization:
            raise HTTPException(status_code=401, detail="token_missing")

        headers = {
            "Accept": "application/json",
            "Authorization": authorization,
        }
        trace_id = get_trace_id(request)
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id

        try:
            async with httpx.AsyncClient(
                timeout=float(self._settings.request_timeout_seconds),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._settings.endpoints.public}/api/v1/auth/me",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="auth_unavailable") from exc

        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="token_invalid")
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="account_disabled")
        if response.status_code >= 500:
            raise HTTPException(status_code=503, detail="auth_unavailable")
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail="auth_failed")

        try:
            payload: Any = response.json()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="auth_invalid_response") from exc

        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
        try:
            user_id = int(data.get("id") or data.get("user_id") or 0)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="token_invalid") from exc
        if user_id <= 0:
            raise HTTPException(status_code=401, detail="token_invalid")

        return AuthContext(
            user_id=user_id,
            role=str(data.get("role") or "user"),
            username=str(data.get("username") or ""),
        )


async def require_auth_context(request: Request) -> AuthContext:
    service = getattr(request.app.state, "gateway_auth_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="auth_service_unavailable")
    return await service.require_auth_context(request)
