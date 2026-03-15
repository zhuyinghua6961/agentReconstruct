"""FastAPI auth routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from server.services.auth_service import auth_service
from server_fastapi.auth.deps import AuthContext, require_auth_context
from server_fastapi.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordInitiateRequest,
    ForgotPasswordVerifyRequest,
    LoginRequest,
    RegisterRequest,
    SetSecurityQuestionsRequest,
)

router = APIRouter()


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=auth_service.status_code_for(result, ok_status=ok_status),
        content=jsonable_encoder(result),
    )


@router.post("/api/v1/auth/login")
@router.post("/api/auth/login")
async def login(payload: LoginRequest):
    return _respond(auth_service.login(payload.username, payload.password), ok_status=200)


@router.post("/api/v1/auth/register")
@router.post("/api/auth/register")
async def register(payload: RegisterRequest):
    return _respond(auth_service.register(payload.username, payload.password), ok_status=201)


@router.get("/api/v1/auth/me")
@router.get("/api/auth/me")
async def me(context: AuthContext = Depends(require_auth_context)):
    return _respond(auth_service.get_user_info(context.user_id), ok_status=200)


@router.put("/api/v1/auth/password")
@router.post("/api/v1/auth/password")
@router.put("/api/auth/password")
@router.post("/api/auth/password")
async def change_password(
    payload: ChangePasswordRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service.change_password(
            user_id=context.user_id,
            old_password=payload.old_password,
            new_password=payload.new_password,
        ),
        ok_status=200,
    )


@router.post("/api/v1/auth/forgot-password/initiate")
@router.post("/api/auth/forgot-password/initiate")
async def forgot_password_initiate(payload: ForgotPasswordInitiateRequest):
    return _respond(auth_service.initiate_password_reset(payload.username), ok_status=200)


@router.post("/api/v1/auth/forgot-password/verify")
@router.post("/api/auth/forgot-password/verify")
async def forgot_password_verify(payload: ForgotPasswordVerifyRequest):
    return _respond(
        auth_service.verify_and_reset_password(
            username=payload.username,
            answers=payload.answers,
            new_password=payload.new_password,
        ),
        ok_status=200,
    )


@router.get("/api/v1/auth/security-questions")
@router.get("/api/auth/security-questions")
async def get_security_questions(context: AuthContext = Depends(require_auth_context)):
    return _respond(auth_service.get_security_questions(user_id=context.user_id), ok_status=200)


@router.put("/api/v1/auth/security-questions")
@router.post("/api/v1/auth/security-questions")
@router.put("/api/auth/security-questions")
@router.post("/api/auth/security-questions")
async def set_security_questions(
    payload: SetSecurityQuestionsRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service.set_security_questions(
            user_id=context.user_id,
            questions=[item.model_dump() for item in payload.questions],
        ),
        ok_status=200,
    )
