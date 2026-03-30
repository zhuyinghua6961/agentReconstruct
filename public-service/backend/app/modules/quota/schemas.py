from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateQuotaConfigRequest(BaseModel):
    quota_type: str = Field(default="")
    quota_name: str = Field(default="")
    default_limit: int = Field(default=0)
    daily_limit: int | None = Field(default=None)
    weekly_limit: int | None = Field(default=None)
    monthly_limit: int | None = Field(default=None)
    is_active: bool = Field(default=True)
    period: str | None = Field(default="daily")
    period_days: int | None = Field(default=None)


class UpdateQuotaConfigRequest(BaseModel):
    default_limit: int = Field(default=0)
    daily_limit: int | None = Field(default=None)
    weekly_limit: int | None = Field(default=None)
    monthly_limit: int | None = Field(default=None)
    is_active: bool = Field(default=True)
    period: str | None = Field(default=None)
    period_days: int | None = Field(default=None)


class InternalQuotaGrantPrecheckRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    quota_type: str = Field(default="")
    strict_config: bool = Field(default=False)


class InternalQuotaGrantFinalizeRequest(BaseModel):
    success: bool = Field(default=False)


class InternalQuotaGrantPrecheckData(BaseModel):
    grant_id: str = Field(default="")
    quota_type: str = Field(default="")
    noop: bool = Field(default=False)
    checked: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=0)


class InternalQuotaGrantFinalizeData(BaseModel):
    grant_id: str = Field(default="")
    quota_type: str = Field(default="")
    noop: bool = Field(default=False)
    counted: bool = Field(default=False)
    idempotent: bool = Field(default=False)
    increment: dict[str, Any] | None = Field(default=None)


class InternalQuotaGrantPrecheckResponse(BaseModel):
    success: bool = Field(default=False)
    data: InternalQuotaGrantPrecheckData | None = Field(default=None)
    error: str | None = Field(default=None)
    code: str | None = Field(default=None)


class InternalQuotaGrantFinalizeResponse(BaseModel):
    success: bool = Field(default=False)
    data: InternalQuotaGrantFinalizeData | None = Field(default=None)
    error: str | None = Field(default=None)
    code: str | None = Field(default=None)
