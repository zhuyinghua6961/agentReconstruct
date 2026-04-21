from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")
    user_type: Literal["common", "super"] | str = Field(default="common")


class UserPasswordResetRequest(BaseModel):
    new_password: str = Field(default="")


class UserStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"] | str = Field(default="active")


class UserTypeUpdateRequest(BaseModel):
    user_type: str | int = Field(default="common")


class UserDepartmentUpdateRequest(BaseModel):
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)


class UserPersonnelBindingUpdateRequest(BaseModel):
    personnel_id: int = Field(gt=0)


class UserUsernameUpdateRequest(BaseModel):
    username: str = Field(default="")


class BatchDeleteUsersRequest(BaseModel):
    user_ids: list[int] = Field(default_factory=list)


class BatchChangeUserTypeRequest(BaseModel):
    user_ids: list[int] = Field(default_factory=list)
    user_type: str | int = Field(default="common")
