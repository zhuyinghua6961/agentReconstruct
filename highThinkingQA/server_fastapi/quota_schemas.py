"""Pydantic schemas for quota APIs."""

from __future__ import annotations

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
