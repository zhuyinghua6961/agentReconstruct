from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PrimaryDepartmentCreateRequest(BaseModel):
    name: str = Field(default="")


class PrimaryDepartmentRenameRequest(BaseModel):
    name: str = Field(default="")


class SecondaryDepartmentCreateRequest(BaseModel):
    primary_department_id: int = Field(default=0)
    name: str = Field(default="")


class SecondaryDepartmentRenameRequest(BaseModel):
    name: str = Field(default="")


class TertiaryDepartmentCreateRequest(BaseModel):
    secondary_department_id: int = Field(default=0)
    name: str = Field(default="")


class TertiaryDepartmentRenameRequest(BaseModel):
    name: str = Field(default="")


class DepartmentStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"] | str = Field(default="active")
