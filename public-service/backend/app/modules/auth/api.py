from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.modules.auth.deps import require_auth_context
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    DepartmentUpdateRequest,
    ForgotPasswordInitiateRequest,
    ForgotPasswordVerifyRequest,
    LoginRequest,
    RegisterRequest,
    SetSecurityQuestionsRequest,
    UsernameUpdateRequest,
)
from app.modules.auth import service as auth_service_module


router = APIRouter(tags=["auth"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=auth_service_module.auth_service.status_code_for(result, ok_status=ok_status),
        content=jsonable_encoder(result),
    )


@router.post("/api/v1/auth/login")
@router.post("/api/auth/login")
def login(payload: LoginRequest):
    return _respond(auth_service_module.auth_service.login(payload.username, payload.password), ok_status=200)


@router.post("/api/v1/auth/register")
@router.post("/api/auth/register")
def register(payload: RegisterRequest):
    return _respond(auth_service_module.auth_service.register(payload.username, payload.password), ok_status=201)


@router.get("/api/v1/auth/me")
@router.get("/api/auth/me")
def me(context: AuthContext = Depends(require_auth_context)):
    return _respond(auth_service_module.auth_service.get_user_info(context.user_id), ok_status=200)


@router.get("/api/v1/auth/departments/tree")
@router.get("/api/auth/departments/tree")
def get_department_tree(context: AuthContext = Depends(require_auth_context)):
    return _respond(auth_service_module.auth_service.get_selectable_department_tree(user_id=context.user_id), ok_status=200)


@router.put("/api/v1/auth/department")
@router.put("/api/auth/department")
def update_department(
    payload: DepartmentUpdateRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service_module.auth_service.update_department(
            user_id=context.user_id,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
        ),
        ok_status=200,
    )


@router.put("/api/v1/auth/username")
@router.put("/api/auth/username")
def update_username(
    payload: UsernameUpdateRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service_module.auth_service.update_username(
            user_id=context.user_id,
            username=payload.username,
        ),
        ok_status=200,
    )


@router.api_route("/api/v1/auth/password", methods=["PUT", "POST"])
@router.api_route("/api/auth/password", methods=["PUT", "POST"])
def change_password(
    payload: ChangePasswordRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service_module.auth_service.change_password(
            user_id=context.user_id,
            old_password=payload.old_password,
            new_password=payload.new_password,
        ),
        ok_status=200,
    )


@router.post("/api/v1/auth/forgot-password/initiate")
@router.post("/api/auth/forgot-password/initiate")
def forgot_password_initiate(payload: ForgotPasswordInitiateRequest):
    return _respond(auth_service_module.auth_service.initiate_password_reset(payload.username), ok_status=200)


@router.post("/api/v1/auth/forgot-password/verify")
@router.post("/api/auth/forgot-password/verify")
def forgot_password_verify(payload: ForgotPasswordVerifyRequest):
    return _respond(
        auth_service_module.auth_service.verify_and_reset_password(
            username=payload.username,
            answers=payload.answers,
            new_password=payload.new_password,
        ),
        ok_status=200,
    )


@router.get("/api/v1/auth/security-questions")
@router.get("/api/auth/security-questions")
def get_security_questions(context: AuthContext = Depends(require_auth_context)):
    return _respond(auth_service_module.auth_service.get_security_questions(user_id=context.user_id), ok_status=200)


@router.api_route("/api/v1/auth/security-questions", methods=["PUT", "POST"])
@router.api_route("/api/auth/security-questions", methods=["PUT", "POST"])
def set_security_questions(
    payload: SetSecurityQuestionsRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        auth_service_module.auth_service.set_security_questions(
            user_id=context.user_id,
            questions=[item.model_dump() for item in payload.questions],
        ),
        ok_status=200,
    )
