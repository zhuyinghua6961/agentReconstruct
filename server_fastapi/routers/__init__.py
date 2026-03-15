"""FastAPI router registration."""

from fastapi import FastAPI

from server_fastapi.routers.ask import router as ask_router
from server_fastapi.routers.admin import router as admin_router
from server_fastapi.routers.auth import router as auth_router
from server_fastapi.routers.conversation import router as conversation_router
from server_fastapi.routers.documents import router as documents_router
from server_fastapi.routers.health import router as health_router
from server_fastapi.routers.ingest import router as ingest_router
from server_fastapi.routers.quota import router as quota_router
from server_fastapi.routers.system import router as system_router
from server_fastapi.routers.upload import router as upload_router


def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(ask_router)
    app.include_router(system_router)
    app.include_router(quota_router)
    app.include_router(conversation_router)
    app.include_router(documents_router)
    app.include_router(ingest_router)
    app.include_router(upload_router)
