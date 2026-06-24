from fastapi import FastAPI

from server_fastapi.routers.ask import router as ask_router
from server_fastapi.routers.health import router as health_router
from server_fastapi.routers.original import router as original_router
from server_fastapi.routers.patent_search import router as patent_search_router



def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(ask_router)
    app.include_router(original_router)
    app.include_router(patent_search_router)
