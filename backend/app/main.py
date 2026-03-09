from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.ws import websocket_router
from app.core.container import build_container
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.dev.seed import seed_demo_environment
from app.models import Base

logger = logging.getLogger(__name__)


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    configure_logging(settings.log_level)

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
        if settings.seed_demo_data:
            report = seed_demo_environment(
                settings,
                if_empty=settings.seed_demo_data_if_empty,
                container=container,
            )
            logger.info(
                "demo seed %s (%s)",
                "applied" if report.seeded else "skipped",
                report.reason,
            )
        logger.info(
            "orchestrator api ready on %s with repos_root=%s workspaces_root=%s",
            settings.environment,
            settings.orchestrator_repos_root,
            settings.orchestrator_workspaces_root,
        )
        yield
        logger.info("orchestrator api shutting down")
        container.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Local-first control plane for manager, worker, and reviewer coding sessions.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        if not settings.log_requests:
            return await call_next(request)

        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (perf_counter() - started_at) * 1000
            logger.exception("http %s %s failed in %.1fms", request.method, request.url.path, duration_ms)
            raise

        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "http %s %s -> %s in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

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
