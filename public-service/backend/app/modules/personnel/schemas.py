from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PersonnelCreateRequest(BaseModel):
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")
    status: Literal["active", "disabled"] | str = Field(default="active")
    remarks: str | None = Field(default=None)


class PersonnelUpdateRequest(BaseModel):
    full_name: str = Field(default="")
    verification_code: str | None = Field(default=None)
    remarks: str | None = Field(default=None)


class PersonnelStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"] | str = Field(default="active")
