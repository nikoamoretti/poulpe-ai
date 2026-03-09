from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Orchestrator API"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_requests: bool = True
    database_url: str = "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"
    sql_echo: bool = False
    auto_create_schema: bool = False
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    startup_check_connections: bool = True
    seed_demo_data: bool = False
    seed_demo_data_if_empty: bool = True
    seed_demo_repo_name: str = "demo-local-agent-repo"
    cors_allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    orchestrator_repos_root: Path = Path(".orchestrator/repos")
    orchestrator_workspaces_root: Path = Path(".orchestrator/workspaces")
    session_heartbeat_interval_seconds: float = 2.0
    session_stop_grace_seconds: float = 1.5
    codex_simulation_mode_default: bool = True
    orchestrator_idle_session_seconds: float = 120.0
    orchestrator_summary_request_cooldown_seconds: float = 120.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def ensure_local_dirs(self) -> None:
        self.orchestrator_repos_root.mkdir(parents=True, exist_ok=True)
        self.orchestrator_workspaces_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
