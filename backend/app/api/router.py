from fastapi import APIRouter

from app.api.routes import billing, events, health, orchestrator, portfolios, projects, reviews, runtime, sessions, tasks, workspaces

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(billing.router)
api_router.include_router(portfolios.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(sessions.router)
api_router.include_router(workspaces.router)
api_router.include_router(reviews.router)
api_router.include_router(events.router)
api_router.include_router(orchestrator.router)
api_router.include_router(runtime.router)
