from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


class DatabaseManager:
    def __init__(self, settings: Settings) -> None:
        connect_args: dict[str, object] = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self.engine: Engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            echo=settings.sql_echo,
            connect_args=connect_args,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def session(self) -> Session:
        return self.session_factory()

    def ping(self) -> bool:
        with self.session_factory() as session:
            session.execute(text("SELECT 1"))
        return True

    def dispose(self) -> None:
        self.engine.dispose()


def build_database_manager(settings: Settings | None = None) -> DatabaseManager:
    return DatabaseManager(settings or get_settings())


def get_db(database: DatabaseManager) -> Generator[Session, None, None]:
    db = database.session()
    try:
        yield db
    finally:
        db.close()
