from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.database import DatabaseManager
from app.core.enums import SessionRole
from app.core.errors import NotFoundError, ValidationError
from app.models.portfolio import Portfolio
from app.models.project import Project
from app.models.project_checkpoint import ProjectCheckpoint
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace


class TaskPacketService:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database
        self.worker_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "worker.md"
        self.project_worker_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "project_worker.md"
        self.manager_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "manager.md"
        self.portfolio_manager_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "portfolio_manager.md"
        self.portfolio_manager_turn_prompt_path = (
            Path(__file__).resolve().parents[1] / "prompts" / "portfolio_manager_turn.md"
        )
        self.manager_review_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "manager_review.md"

    def build_manager_packet(
        self,
        session_id: UUID,
        *,
        goal: str,
    ) -> str:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.role != SessionRole.MANAGER:
                raise ValidationError("Only manager sessions support manager packets.")

            project = db.get(Project, session.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {session.project_id}")

        manager_prompt = self.manager_prompt_path.read_text(encoding="utf-8").strip()

        lines = [
            manager_prompt,
            "",
            "---",
            "",
            "Context:",
            f"Project: {project.name}",
            f"Repo path: {project.repo_path}",
            f"Default branch: {project.default_branch}",
            "",
            "Operator goal:",
            goal.strip(),
            "",
            "Analyze this goal, inspect the repo if needed, then emit your task plan.",
        ]
        return "\n".join(lines).strip() + "\n"

    def build_portfolio_manager_packet(
        self,
        session_id: UUID,
        *,
        goal: str,
    ) -> str:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.role != SessionRole.MANAGER or session.portfolio_id is None:
                raise ValidationError("Only portfolio manager sessions support portfolio manager packets.")

            portfolio = db.get(Portfolio, session.portfolio_id)
            if portfolio is None:
                raise NotFoundError(f"Portfolio not found: {session.portfolio_id}")

            projects = db.scalars(
                select(Project).where(Project.portfolio_id == portfolio.id).order_by(Project.created_at.asc())
            ).all()

        manager_prompt = self.portfolio_manager_prompt_path.read_text(encoding="utf-8").strip()
        lines = [
            manager_prompt,
            "",
            "---",
            "",
            "Portfolio:",
            f"Name: {portfolio.name}",
            f"Goal: {portfolio.goal or '(none provided)'}",
            f"Manager workspace path: {session.workspace_path or '(none assigned)'}",
            "",
            "Projects:",
        ]
        if projects:
            for project in projects:
                lines.extend(
                    [
                        f"- {project.name}",
                        f"  repo_path: {project.repo_path}",
                        f"  default_branch: {project.default_branch}",
                        f"  objective: {project.objective or '(none provided)'}",
                        f"  status: {project.status.value}",
                        f"  worker_session_id: {project.worker_session_id or '(not started)'}",
                    ]
                )
        else:
            lines.append("- No projects have been created in this portfolio yet.")

        lines.extend(
            [
                "",
                "Operator goal:",
                goal.strip(),
                "",
                "Supervise the portfolio, answer project questions, and keep work moving toward completion.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def build_portfolio_manager_turn_packet(self, session_id: UUID) -> str:
        portfolio, project, checkpoint, machine_context = self._portfolio_manager_turn_context(session_id)
        turn_prompt = self.portfolio_manager_turn_prompt_path.read_text(encoding="utf-8").strip()
        lines = [
            f"PORTFOLIO_MANAGER_TURN_JSON: {json.dumps(machine_context, sort_keys=True)}",
            turn_prompt,
            "",
            "---",
            "",
            "Portfolio:",
            f"Name: {portfolio.name}",
            f"Goal: {portfolio.goal or '(none provided)'}",
            "",
            "Project:",
            f"Name: {project.name}",
            f"Repo path: {project.repo_path}",
            f"Default branch: {project.default_branch}",
            "Objective:",
            project.objective.strip() or "(none provided)",
            "",
            "Checkpoint:",
            f"Id: {checkpoint.id}",
            f"Kind: {checkpoint.kind.value}",
            f"Summary: {checkpoint.summary}",
            f"Occurred at: {checkpoint.source_occurred_at.isoformat()}",
            "",
            "Checkpoint details JSON:",
            json.dumps(checkpoint.details_json, indent=2, sort_keys=True, default=str),
            "",
            "Decision rules:",
            "- If the checkpoint kind is question, blocked, or error: choose result=answer unless dismissal is clearly safer.",
            "- If the checkpoint kind is completion: choose result=approve only when the project objective looks satisfied by the attached review context; otherwise choose result=request_changes.",
            "- If you request changes or answer a question, response_message must be a direct instruction to the project worker.",
            "- Keep response_message concise and operational.",
            "",
            "Required output:",
            "Emit exactly one final complete event with this shape:",
            '[[EVENT]] {"type":"complete","summary":"Decision ready","result":"approve|request_changes|answer|dismiss","response_message":"...","details":{"notes":"optional"}} [[/EVENT]]',
            "",
            "Do not implement code. Decide, explain briefly, and stop.",
        ]
        return "\n".join(lines).strip() + "\n"

    def build_portfolio_manager_turn_simulation_message(self, session_id: UUID) -> str:
        _, _, _, machine_context = self._portfolio_manager_turn_context(session_id)
        return f"PORTFOLIO_MANAGER_TURN_JSON: {json.dumps(machine_context, sort_keys=True)}"

    def _portfolio_manager_turn_context(
        self,
        session_id: UUID,
    ) -> tuple[Portfolio, Project, ProjectCheckpoint, dict[str, object]]:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.role != SessionRole.MANAGER or session.portfolio_id is None:
                raise ValidationError("Only portfolio manager turn sessions support manager turn packets.")

            metadata = dict(session.metadata_json)
            checkpoint_id = str(
                metadata.get("checkpoint_id")
                or (metadata.get("automation", {}) or {}).get("checkpoint_id")
                or ""
            ).strip()
            if not checkpoint_id:
                raise ValidationError(f"Manager turn session {session_id} is missing a checkpoint_id.")

            portfolio = db.get(Portfolio, session.portfolio_id)
            if portfolio is None:
                raise NotFoundError(f"Portfolio not found: {session.portfolio_id}")

            checkpoint = db.get(ProjectCheckpoint, UUID(checkpoint_id))
            if checkpoint is None:
                raise NotFoundError(f"Checkpoint not found: {checkpoint_id}")

            project = db.get(Project, checkpoint.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {checkpoint.project_id}")
        review_context = checkpoint.details_json.get("review_context", {})
        if not isinstance(review_context, dict):
            review_context = {}
        diff_info = review_context.get("diff", {})
        if not isinstance(diff_info, dict):
            diff_info = {}
        checks = review_context.get("checks", [])
        if not isinstance(checks, list):
            checks = []

        machine_context = {
            "portfolio_id": str(portfolio.id),
            "project_id": str(project.id),
            "checkpoint_id": str(checkpoint.id),
            "checkpoint_kind": checkpoint.kind.value,
            "checkpoint_summary": checkpoint.summary,
            "project_name": project.name,
            "project_objective": project.objective,
            "repo_path": project.repo_path,
            "worker_session_id": str(project.worker_session_id) if project.worker_session_id else None,
            "diff_file_count": int(diff_info.get("file_count") or 0),
            "changed_files": list(diff_info.get("changed_files") or []),
            "has_review_context_error": bool(review_context.get("error")),
            "check_count": len(checks),
        }
        return portfolio, project, checkpoint, machine_context

    def build_worker_packet(
        self,
        session_id: UUID,
        *,
        operator_note: str | None = None,
    ) -> str:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.role != SessionRole.WORKER:
                raise ValidationError("Only worker sessions support Codex task packets.")

            task = db.get(Task, session.task_id) if session.task_id is not None else None
            if task is None:
                raise ValidationError(f"Worker session {session_id} has no task to execute.")

            project = db.get(Project, session.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {session.project_id}")

            workspace = None
            if session.workspace_path:
                workspace = db.query(Workspace).filter(Workspace.session_id == session.id).one_or_none()

        allowed_paths = self._allowed_paths(session=session, task=task)
        acceptance_criteria = [item.strip() for item in task.acceptance_criteria if item and item.strip()]
        worker_prompt = self.worker_prompt_path.read_text(encoding="utf-8").strip()

        lines = [
            worker_prompt,
            "",
            "Task packet:",
            f"Project: {project.name}",
            f"Repo path: {project.repo_path}",
            f"Default branch: {project.default_branch}",
            f"Task id: {task.id}",
            f"Task title: {task.title}",
            "Task description:",
            task.description.strip() or "(none provided)",
        ]

        if workspace is not None:
            lines.extend(
                [
                    f"Workspace path: {workspace.workspace_path}",
                    f"Workspace branch: {workspace.branch_name}",
                    f"Workspace base branch: {workspace.base_branch}",
                    f"Workspace base commit: {workspace.base_commit}",
                ]
            )
        elif session.workspace_path:
            lines.append(f"Workspace path: {session.workspace_path}")

        lines.append("Scope:")
        if allowed_paths:
            lines.extend(f"- {path}" for path in allowed_paths)
        else:
            lines.append("- Anywhere in the assigned workspace")

        lines.append("Acceptance criteria:")
        if acceptance_criteria:
            lines.extend(f"- {criterion}" for criterion in acceptance_criteria)
        else:
            lines.append("- Complete the requested task safely and verify the result when practical.")

        # Include manager feedback for revision rounds
        task_orch = task.metadata_json.get("orchestrator", {})
        if isinstance(task_orch, dict):
            manager_feedback = task_orch.get("manager_feedback")
            if isinstance(manager_feedback, list) and manager_feedback:
                lines.append("Manager review feedback (address these issues):")
                for item in manager_feedback:
                    lines.append(f"- {item}")

        if operator_note and operator_note.strip():
            lines.extend(
                [
                    "Operator kickoff:",
                    operator_note.strip(),
                ]
            )

        lines.extend(
            [
                "Execution requirements:",
                "- Work only inside the assigned workspace path.",
                "- Stay within the listed scope unless the task explicitly requires more.",
                "- Inspect the repository before editing and keep changes narrowly scoped to the task.",
                "- Emit exact [[EVENT]] JSON blocks as defined above.",
                "- Emit progress after meaningful implementation steps, tests_run after verification, and complete when finished.",
                "- If you cannot continue, emit blocked or error with a concrete reason.",
                "- Stop when the task is complete. Review and merge will be handled separately.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def build_project_packet(
        self,
        session_id: UUID,
        *,
        operator_note: str | None = None,
    ) -> str:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.role != SessionRole.WORKER or session.project_id is None:
                raise ValidationError("Only project worker sessions support project packets.")

            project = db.get(Project, session.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {session.project_id}")

            workspace = None
            if session.workspace_path:
                workspace = db.query(Workspace).filter(Workspace.session_id == session.id).one_or_none()

        project_prompt = self.project_worker_prompt_path.read_text(encoding="utf-8").strip()
        lines = [
            project_prompt,
            "",
            "Project packet:",
            f"Project: {project.name}",
            f"Repo path: {project.repo_path}",
            f"Default branch: {project.default_branch}",
            "Objective:",
            project.objective.strip() or "(none provided)",
        ]

        if workspace is not None:
            lines.extend(
                [
                    f"Workspace path: {workspace.workspace_path}",
                    f"Workspace branch: {workspace.branch_name}",
                    f"Workspace base branch: {workspace.base_branch}",
                    f"Workspace base commit: {workspace.base_commit}",
                ]
            )
        elif session.workspace_path:
            lines.append(f"Workspace path: {session.workspace_path}")

        if operator_note and operator_note.strip():
            lines.extend(
                [
                    "Operator kickoff:",
                    operator_note.strip(),
                ]
            )

        lines.extend(
            [
                "Execution requirements:",
                "- Operate independently as the single agent responsible for this project.",
                "- Work only inside the assigned workspace path.",
                "- Inspect the repository before editing and keep changes aligned to the project objective.",
                "- Ask concise questions when a decision or clarification is needed.",
                "- Emit exact [[EVENT]] JSON blocks as defined above.",
                "- Emit progress after meaningful implementation steps, tests_run after verification, and complete when finished.",
                "- If you cannot continue, emit blocked or error with a concrete reason.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def build_manager_review_packet(
        self,
        session_id: UUID,
        *,
        diff: str,
        changed_files: list[str],
    ) -> str:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")

            task = db.get(Task, session.task_id) if session.task_id is not None else None
            if task is None:
                raise ValidationError(f"Review session {session_id} has no task to review.")

            project = db.get(Project, session.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {session.project_id}")

        review_prompt = self.manager_review_prompt_path.read_text(encoding="utf-8").strip()
        acceptance_criteria = [c.strip() for c in task.acceptance_criteria if c and c.strip()]

        lines = [
            review_prompt,
            "",
            "---",
            "",
            f"Project: {project.name}",
            f"Task: {task.title}",
            f"Description: {task.description.strip() or '(none)'}",
            "",
            "Acceptance criteria:",
        ]
        if acceptance_criteria:
            lines.extend(f"- {c}" for c in acceptance_criteria)
        else:
            lines.append("- Complete the requested task safely.")

        lines.extend([
            "",
            f"Changed files ({len(changed_files)}):",
        ])
        for f in changed_files[:20]:
            lines.append(f"- {f}")
        if len(changed_files) > 20:
            lines.append(f"- ... and {len(changed_files) - 20} more")

        lines.extend([
            "",
            "Diff:",
            "```diff",
            diff[:8000] if len(diff) <= 8000 else diff[:8000] + "\n... (truncated)",
            "```",
            "",
            "Review this diff against the acceptance criteria and emit your verdict.",
        ])
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _allowed_paths(*, session: SessionModel, task: Task) -> list[str]:
        session_assignment = session.metadata_json.get("assignment")
        if isinstance(session_assignment, dict):
            allowed_paths = session_assignment.get("allowed_paths")
            if isinstance(allowed_paths, list):
                normalized = [str(path).strip() for path in allowed_paths if str(path).strip()]
                if normalized:
                    return normalized

        task_orchestration = task.metadata_json.get("orchestrator")
        if isinstance(task_orchestration, dict):
            allowed_paths = task_orchestration.get("allowed_paths")
            if isinstance(allowed_paths, list):
                normalized = [str(path).strip() for path in allowed_paths if str(path).strip()]
                if normalized:
                    return normalized

        task_request = task.metadata_json.get("request")
        if isinstance(task_request, dict):
            scope = task_request.get("scope")
            if isinstance(scope, list):
                return [str(path).strip() for path in scope if str(path).strip()]

        return []
