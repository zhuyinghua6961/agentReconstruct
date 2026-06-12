from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PersonnelCreateRequest(BaseModel):
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)
    status: Literal["active", "disabled"] | str = Field(default="active")
    remarks: str | None = Field(default=None)


class PersonnelUpdateRequest(BaseModel):
    full_name: str = Field(default="")
    verification_code: str | None = Field(default=None)
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)
    status: Literal["active", "disabled"] | str | None = Field(default=None)
    remarks: str | None = Field(default=None)


class PersonnelStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"] | str = Field(default="active")


class PersonnelBatchDeleteRequest(BaseModel):
    personnel_ids: list[int] = Field(default_factory=list)


class PersonnelBatchStatusUpdateRequest(BaseModel):
    personnel_ids: list[int] = Field(default_factory=list)
    status: Literal["active", "disabled"] | str = Field(default="active")


class PersonnelBatchDepartmentUpdateRequest(BaseModel):
    personnel_ids: list[int] = Field(default_factory=list)
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)


class PersonnelForceDeleteRequest(BaseModel):
    admin_password: str = Field(default="")


class PersonnelBatchForceDeleteRequest(BaseModel):
    personnel_ids: list[int] = Field(default_factory=list)
    admin_password: str = Field(default="")
