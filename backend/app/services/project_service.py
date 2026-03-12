from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.config import Settings
from app.core.enums import EventCategory, EventLevel, ProjectStatus, SessionRole, SessionStatus
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.text import slugify
from app.models.portfolio import Portfolio
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.workspace import Workspace
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.project import ProjectCreate, ProjectRead, ProjectStartRequest
from app.schemas.session import SessionRead
from app.services.command_runner import CommandRunner
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeService
from app.services.session_supervisor import SessionSupervisor
from app.services.workspace_service import WorkspaceService


class ProjectService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        event_service: EventService,
        command_runner: CommandRunner,
        repo_inspector: RepoInspectorAdapter,
        runtime_service: RuntimeService,
        session_supervisor: SessionSupervisor,
        workspace_service: WorkspaceService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.event_service = event_service
        self.command_runner = command_runner
        self.repo_inspector = repo_inspector
        self.runtime_service = runtime_service
        self.session_supervisor = session_supervisor
        self.workspace_service = workspace_service

    def list_projects(self, portfolio_id: UUID | None = None) -> list[ProjectRead]:
        stmt = select(Project).order_by(Project.created_at.desc())
        if portfolio_id is not None:
            stmt = stmt.where(Project.portfolio_id == portfolio_id)
        records = self.db.scalars(stmt).all()
        return [ProjectRead.model_validate(record) for record in records]

    def get_project(self, project_id: UUID) -> ProjectRead:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")
        return ProjectRead.model_validate(project)

    def list_scope_options(self, project_id: UUID) -> list[str]:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")

        repo_path = Path(project.repo_path).expanduser().resolve()
        entries = [
            path.name
            for path in repo_path.iterdir()
            if path.name != ".git" and not path.name.startswith(".")
        ]
        return sorted(
            entries,
            key=lambda entry: (
                1 if (repo_path / entry).is_file() else 0,
                entry.lower(),
            ),
        )

    def create_project(self, payload: ProjectCreate) -> ProjectRead:
        repo_info = self._resolve_repo(payload)
        objective = self._normalized_objective(payload)
        existing = self.db.scalar(select(Project).where(Project.repo_path == repo_info.repo_path))
        if existing is not None:
            raise ConflictError(f"Project already exists for repo_path={repo_info.repo_path}")

        if payload.portfolio_id is not None:
            portfolio = self.db.get(Portfolio, payload.portfolio_id)
            if portfolio is None:
                raise NotFoundError(f"Portfolio not found: {payload.portfolio_id}")

        project = Project(
            portfolio_id=payload.portfolio_id,
            name=payload.name,
            slug=self._build_unique_slug(payload.name),
            repo_path=repo_info.repo_path,
            default_branch=payload.default_branch or repo_info.default_branch,
            objective=objective,
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
                    "portfolio_id": str(project.portfolio_id) if project.portfolio_id else None,
                    "repo_path": project.repo_path,
                    "default_branch": project.default_branch,
                    "objective": project.objective,
                    "repo_created": payload.create_repo,
                },
            )
        )
        return ProjectRead.model_validate(project)

    def _normalized_objective(self, payload: ProjectCreate) -> str:
        objective = payload.objective.strip()
        if objective:
            return objective
        return f"Work independently on {payload.name} and bring it to completion."

    def _resolve_repo(self, payload: ProjectCreate):
        fallback_branch = payload.default_branch or "main"
        if payload.create_repo:
            return self._create_repo_for_project(payload, fallback_branch=fallback_branch)
        if not payload.repo_path or not payload.repo_path.strip():
            raise ValidationError("repo_path is required unless create_repo is true.")
        return self.repo_inspector.inspect(
            payload.repo_path,
            fallback_branch=fallback_branch,
        )

    def _create_repo_for_project(self, payload: ProjectCreate, *, fallback_branch: str):
        repos_root = self.settings.orchestrator_repos_root / "projects"
        repos_root.mkdir(parents=True, exist_ok=True)
        repo_path = self._next_repo_path(repos_root, payload.name)
        repo_path.mkdir(parents=True, exist_ok=False)
        objective = self._normalized_objective(payload)

        readme = [
            f"# {payload.name}",
            "",
            objective,
            "",
        ]
        (repo_path / "README.md").write_text("\n".join(readme), encoding="utf-8")

        self.command_runner.run(["git", "init", "-b", fallback_branch], cwd=repo_path)
        self.command_runner.run(["git", "add", "README.md"], cwd=repo_path)
        self.command_runner.run(
            [
                "git",
                "-c",
                "user.name=Poulpe",
                "-c",
                "user.email=poulpe@local",
                "commit",
                "-m",
                "Initial commit",
            ],
            cwd=repo_path,
        )
        return self.repo_inspector.inspect(str(repo_path), fallback_branch=fallback_branch)

    def _next_repo_path(self, repos_root: Path, project_name: str) -> Path:
        base = slugify(project_name) or "project"
        candidate = repos_root / base
        counter = 2
        while candidate.exists():
            candidate = repos_root / f"{base}-{counter}"
            counter += 1
        return candidate

    def start_project(self, project_id: UUID, payload: ProjectStartRequest) -> SessionRead:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")

        previous_session = self._project_worker_session_record(project)
        session = previous_session
        if session is None or session.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            session = self._create_project_worker_session(
                project,
                payload,
                reuse_workspace_from=previous_session,
            )

        if session.status == SessionStatus.PENDING:
            self.workspace_service.provision_session_workspace(session.id)
            kickoff = payload.initial_message or project.objective or f"Execute project: {project.name}"
            self.session_supervisor.start_session(session.id, initial_message=kickoff)

        self.db.expire_all()
        session_record = self.db.get(SessionModel, session.id)
        if session_record is None:
            raise NotFoundError(f"Session not found: {session.id}")

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="project.execution_started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", role=SessionRole.WORKER, id="projects.start"),
                project_id=project.id,
                session_id=session_record.id,
                payload={
                    "portfolio_id": str(project.portfolio_id) if project.portfolio_id else None,
                    "worker_session_id": str(session_record.id),
                    "runtime_provider": self.runtime_service.runtime_from_metadata(
                        session_record.metadata_json
                    ).resolved_provider,
                },
            )
        )
        return self._session_read(session_record)

    def deliver_manager_instruction(
        self,
        project_id: UUID,
        *,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRead:
        normalized = message.strip()
        if not normalized:
            raise ValidationError("Manager instruction cannot be empty.")

        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")

        session = self._project_worker_session_record(project)
        if session is None or session.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            restart_payload = self._restart_payload(session, normalized, metadata or {})
            return self.start_project(
                project_id,
                restart_payload,
            )

        if session.status == SessionStatus.PENDING:
            return self.start_project(
                project_id,
                ProjectStartRequest(
                    initial_message=normalized,
                    metadata=metadata or {},
                ),
            )

        runtime = self.runtime_service.runtime_from_metadata(session.metadata_json)
        if runtime.simulated:
            self.session_supervisor.send(session.id, normalized)
            self.db.expire_all()
            session_record = self.db.get(SessionModel, session.id)
            if session_record is None:
                raise NotFoundError(f"Session not found: {session.id}")
            return self._session_read(session_record)

        self.session_supervisor.stop(session.id)
        restart_payload = self._restart_payload(session, normalized, metadata or {})
        replacement = self._create_project_worker_session(
            project,
            restart_payload,
            reuse_workspace_from=session,
        )
        self.workspace_service.provision_session_workspace(replacement.id)
        self.session_supervisor.start_session(replacement.id, initial_message=normalized)
        self.db.expire_all()
        session_record = self.db.get(SessionModel, replacement.id)
        if session_record is None:
            raise NotFoundError(f"Session not found: {replacement.id}")
        return self._session_read(session_record)

    def _create_project_worker_session(
        self,
        project: Project,
        payload: ProjectStartRequest,
        *,
        reuse_workspace_from: SessionModel | None = None,
    ) -> SessionModel:
        portfolio = self.db.get(Portfolio, project.portfolio_id) if project.portfolio_id is not None else None
        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.WORKER,
            command_override=payload.command_override,
            runtime_preference=payload.runtime_preference,
            allow_simulation_fallback=payload.allow_simulation_fallback,
            simulation_mode=payload.simulation_mode,
        )
        metadata = dict(payload.metadata)
        if payload.model is not None:
            metadata["model"] = payload.model
        metadata.update(
            {
                "session_kind": "project_worker",
                "objective": project.objective,
                "preferred_engine": payload.runtime_preference or metadata.get("preferred_engine") or "auto",
                "allow_simulation_fallback": payload.allow_simulation_fallback,
                "simulation_mode": launch_plan.simulation_mode,
                "launch_notes": launch_plan.notes,
                "runtime": launch_plan.runtime.model_dump(mode="json"),
            }
        )
        session = SessionModel(
            portfolio_id=project.portfolio_id,
            project_id=project.id,
            supervisor_session_id=portfolio.manager_session_id if portfolio is not None else None,
            role=SessionRole.WORKER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        self.db.add(session)
        self.db.flush()

        self._adopt_workspace(reuse_workspace_from, session)
        project.worker_session_id = session.id
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(session)
        return session

    def _project_worker_session_record(self, project: Project) -> SessionModel | None:
        if project.worker_session_id is not None:
            session = self.db.get(SessionModel, project.worker_session_id)
            if session is not None:
                return session
        return self.db.scalar(
            select(SessionModel)
            .where(
                SessionModel.project_id == project.id,
                SessionModel.role == SessionRole.WORKER,
                SessionModel.task_id.is_(None),
            )
            .order_by(SessionModel.created_at.desc())
        )

    def _session_read(self, session: SessionModel) -> SessionRead:
        payload = SessionRead.model_validate(session).model_dump(mode="python")
        payload["runtime"] = self.runtime_service.runtime_from_metadata(session.metadata_json)
        return SessionRead.model_validate(payload)

    @staticmethod
    def _restart_payload(
        previous_session: SessionModel | None,
        message: str,
        metadata: dict[str, Any],
    ) -> ProjectStartRequest:
        if previous_session is None:
            return ProjectStartRequest(initial_message=message, metadata=metadata)

        preferred_engine = previous_session.metadata_json.get("preferred_engine")
        allow_simulation_fallback = previous_session.metadata_json.get("allow_simulation_fallback")
        simulation_mode = previous_session.metadata_json.get("simulation_mode")
        model = previous_session.metadata_json.get("model")
        return ProjectStartRequest(
            command_override=previous_session.command,
            runtime_preference=str(preferred_engine) if preferred_engine else None,
            allow_simulation_fallback=(
                bool(allow_simulation_fallback) if allow_simulation_fallback is not None else None
            ),
            simulation_mode=bool(simulation_mode) if simulation_mode is not None else None,
            model=str(model) if model else None,
            initial_message=message,
            metadata=metadata,
        )

    def _adopt_workspace(self, previous_session: SessionModel | None, session: SessionModel) -> None:
        if previous_session is None:
            return

        workspace = self.db.scalar(select(Workspace).where(Workspace.session_id == previous_session.id))
        if workspace is not None:
            metadata = dict(workspace.metadata_json)
            ownership = metadata.get("ownership")
            if not isinstance(ownership, dict):
                ownership = {}
            ownership.update(
                {
                    "session_id": str(session.id),
                    "path_lock_owner": str(session.id),
                    "previous_session_id": str(previous_session.id),
                }
            )
            metadata["ownership"] = ownership
            metadata["adopted_from_session_id"] = str(previous_session.id)
            workspace.session_id = session.id
            workspace.metadata_json = metadata
            session.branch_name = workspace.branch_name
            session.workspace_path = workspace.workspace_path
            return

        if previous_session.workspace_path:
            session.workspace_path = previous_session.workspace_path
        if previous_session.branch_name:
            session.branch_name = previous_session.branch_name

    def _build_unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        counter = 2
        while self.db.scalar(select(Project.id).where(Project.slug == slug)) is not None:
            slug = f"{base}-{counter}"
            counter += 1
        return slug
