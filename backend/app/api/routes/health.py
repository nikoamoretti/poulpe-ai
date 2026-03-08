from fastapi import APIRouter, Request

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    container = request.app.state.container
    checks = container.health_checks()
    overall = "ok" if checks["database"] == "ok" and checks["redis"] in {"ok", "disabled"} else "degraded"
    return HealthResponse(
        status=overall,
        service=container.settings.app_name,
        version=container.settings.app_version,
        checks=checks,
    )
