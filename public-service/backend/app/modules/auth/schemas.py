from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


class RegisterRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")
    security_questions: list[SecurityQuestionItem] = Field(default_factory=list)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(default="")
    new_password: str = Field(default="")


class ForgotPasswordInitiateRequest(BaseModel):
    username: str = Field(default="")


class ForgotPasswordVerifyRequest(BaseModel):
    username: str = Field(default="")
    answers: list[Any] = Field(default_factory=list)
    new_password: str = Field(default="")


class SecurityQuestionItem(BaseModel):
    question: str = Field(default="")
    answer: str = Field(default="")


class SetSecurityQuestionsRequest(BaseModel):
    questions: list[SecurityQuestionItem] = Field(default_factory=list)


class DepartmentUpdateRequest(BaseModel):
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)


class PersonnelBindingUpdateRequest(BaseModel):
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")


class UsernameUpdateRequest(BaseModel):
    username: str = Field(default="")
