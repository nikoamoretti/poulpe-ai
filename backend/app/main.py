from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.ws import websocket_router
from app.core.container import build_container
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.models import Base

configure_logging()


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = build_container(settings)
        container.ensure_local_dirs()
        if settings.auto_create_schema:
            Base.metadata.create_all(container.database.engine)
        app.state.container = container
        app.state.settings = settings
        if settings.startup_check_connections:
            container.health_checks()
        yield
        container.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Local-first control plane for manager, worker, and reviewer coding sessions.",
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(websocket_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    return app


app = create_app()
