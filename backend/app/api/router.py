from fastapi import APIRouter

from app.api.routes import events, health, projects, reviews, sessions, tasks, workspaces

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(sessions.router)
api_router.include_router(workspaces.router)
api_router.include_router(reviews.router)
api_router.include_router(events.router)
