from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.runtime import lifespan
from app.modules.admin_users.api import router as admin_users_router
from app.modules.auth.api import router as auth_router
from app.modules.conversation.api import router as conversation_router
from app.modules.conversation.internal_api import router as conversation_internal_router
from app.modules.departments.api import router as departments_router
from app.modules.documents.api import router as documents_router
from app.modules.personnel.api import router as personnel_router
from app.modules.quota.api import router as quota_router
from app.modules.system.api import router as system_router
from app.modules.uploads.api import router as uploads_router


DEFAULT_ROUTERS: tuple[APIRouter, ...] = (
    system_router,
    auth_router,
    admin_users_router,
    departments_router,
    personnel_router,
    quota_router,
    conversation_router,
    conversation_internal_router,
    documents_router,
    uploads_router,
)


def create_app(
    *,
    settings: Settings | None = None,
    routers: Iterable[APIRouter] | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(debug=app_settings.debug)

    app = FastAPI(
        title=app_settings.app_name,
        debug=app_settings.debug,
        docs_url=app_settings.docs_url,
        openapi_url=app_settings.openapi_url,
        lifespan=lifespan,
    )
    app.state.settings = app_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=app_settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    @app.get("/")
    def index() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "service": app_settings.app_name,
                "status": "ok",
                "mode": "service",
                "modules": [
                    "system",
                    "auth",
                    "admin_users",
                    "departments",
                    "personnel",
                    "quota",
                    "conversation",
                    "documents",
                    "uploads",
                ],
            },
        )

    for router in routers if routers is not None else DEFAULT_ROUTERS:
        app.include_router(router)

    return app


app = create_app()
