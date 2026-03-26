from fastapi import FastAPI

from server_fastapi.routers.ask import router as ask_router
from server_fastapi.routers.health import router as health_router



def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(ask_router)
