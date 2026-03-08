from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.enums import EventCategory, EventLevel, ProjectStatus
from app.core.errors import ConflictError, NotFoundError
from app.core.text import slugify
from app.models.project import Project
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.project import ProjectCreate, ProjectRead
from app.services.event_service import EventService


class ProjectService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        repo_inspector: RepoInspectorAdapter,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.repo_inspector = repo_inspector

    def list_projects(self) -> list[ProjectRead]:
        records = self.db.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return [ProjectRead.model_validate(record) for record in records]

    def get_project(self, project_id: UUID) -> ProjectRead:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")
        return ProjectRead.model_validate(project)

    def create_project(self, payload: ProjectCreate) -> ProjectRead:
        repo_info = self.repo_inspector.inspect(
            payload.repo_path,
            fallback_branch=payload.default_branch or "main",
        )
        existing = self.db.scalar(select(Project).where(Project.repo_path == repo_info.repo_path))
        if existing is not None:
            raise ConflictError(f"Project already exists for repo_path={repo_info.repo_path}")

        project = Project(
            name=payload.name,
            slug=self._build_unique_slug(payload.name),
            repo_path=repo_info.repo_path,
            default_branch=payload.default_branch or repo_info.default_branch,
            status=ProjectStatus.ACTIVE,
            metadata_json=payload.metadata,
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="project.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="projects.create"),
                project_id=project.id,
                payload={
                    "name": project.name,
                    "repo_path": project.repo_path,
                    "default_branch": project.default_branch,
                },
            )
        )
        return ProjectRead.model_validate(project)

    def _build_unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        counter = 2
        while self.db.scalar(select(Project.id).where(Project.slug == slug)) is not None:
            slug = f"{base}-{counter}"
            counter += 1
        return slug
