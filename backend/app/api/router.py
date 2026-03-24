from fastapi import APIRouter

from app.api.routes import businesses, checkpoints, events, health, inbound, orchestrator, portfolios, projects, reviews, runtime, sessions, stripe_webhook, tasks, workspace_files, workspaces

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(portfolios.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(sessions.router)
api_router.include_router(workspaces.router)
api_router.include_router(reviews.router)
api_router.include_router(events.router)
api_router.include_router(orchestrator.router)
api_router.include_router(runtime.router)
api_router.include_router(workspace_files.router)
api_router.include_router(checkpoints.router)
api_router.include_router(businesses.router)
api_router.include_router(stripe_webhook.router)
api_router.include_router(inbound.router)
