"""Pydantic request models for auth routes."""

# Deprecated: retained only for the retired highThinkingQA auth router.


from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


class RegisterRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


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
