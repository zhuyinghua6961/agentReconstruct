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
