from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ArtifactKind, EventCategory, EventLevel, ReviewStatus, SessionRole, TaskStatus
from app.core.errors import NotFoundError, ValidationError
from app.models.artifact import Artifact
from app.models.review import Review
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.review import (
    ReviewApprovalRead,
    ReviewApprove,
    ReviewArtifactRead,
    ReviewCheckRead,
    ReviewCreate,
    ReviewDiffSummaryRead,
    ReviewMergeReady,
    ReviewRead,
    ReviewReject,
)
from app.schemas.workspace import WorkspaceCommandRead, WorkspaceCommandRequest, WorkspaceDiffRead
from app.services.event_service import EventService
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        workspace_service: WorkspaceService,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.workspace_service = workspace_service
        self.reviewer_prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "reviewer.md"

    def list_reviews(self, project_id: UUID | None = None) -> list[ReviewRead]:
        stmt = select(Review).order_by(Review.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(Review.project_id == project_id)
        records = self.db.scalars(stmt).all()
        return [self._build_review_read(record) for record in records]

    def get_review(self, review_id: UUID) -> ReviewRead:
        review = self._get_review_record(review_id)
        return self._build_review_read(review)

    def create_review(self, payload: ReviewCreate) -> ReviewRead:
        task = self._resolve_task(payload)
        worker_session = self._resolve_worker_session(task=task, session_id=payload.session_id)
        reviewer_session = self._resolve_reviewer_session(task.project_id, payload.reviewer_session_id)
        workspace = self._resolve_workspace(worker_session.id)

        diff = self.workspace_service.get_diff(workspace.id)
        lint_result = self._run_optional_check(workspace.id, kind="lint", command=payload.lint_command)
        test_result = self._run_optional_check(workspace.id, kind="tests", command=payload.test_command)

        diff_summary = self._summarize_diff(diff)
        status = ReviewStatus.RUNNING if reviewer_session is not None else ReviewStatus.PENDING
        prompt_text = self.reviewer_prompt_path.read_text(encoding="utf-8")

        review = Review(
            project_id=task.project_id,
            task_id=task.id,
            requester_session_id=payload.requester_session_id or worker_session.id,
            reviewer_session_id=reviewer_session.id if reviewer_session is not None else None,
            status=status,
            summary=payload.summary or diff_summary["summary"],
            lint_status=lint_result["status"] if lint_result is not None else None,
            test_status=test_result["status"] if test_result is not None else None,
            metadata_json={
                **payload.metadata,
                "worker_session_id": str(worker_session.id),
                "workspace_id": str(workspace.id),
                "changed_files": diff.changed_files,
                "diff_summary": diff_summary,
                "reviewer_notes": None,
                "approval": {
                    "merge_ready": False,
                    "merge_ready_by": None,
                    "merge_ready_at": None,
                    "note": None,
                },
            },
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)

        diff_artifact = self._create_diff_artifact(review, diff, diff_summary)
        lint_artifact = self._create_check_artifact(review, lint_result, kind=ArtifactKind.LINT_REPORT)
        test_artifact = self._create_check_artifact(review, test_result, kind=ArtifactKind.TEST_REPORT)

        review_packet = self._build_review_packet(
            review=review,
            task=task,
            worker_session=worker_session,
            reviewer_session=reviewer_session,
            workspace=workspace,
            diff=diff,
            diff_summary=diff_summary,
            lint_result=lint_result,
            test_result=test_result,
            prompt_text=prompt_text,
        )

        metadata = dict(review.metadata_json)
        metadata["prompt_template_path"] = str(self.reviewer_prompt_path)
        metadata["review_packet"] = review_packet
        metadata["artifacts"] = {
            "diff_artifact_id": str(diff_artifact.id),
            "lint_artifact_id": str(lint_artifact.id) if lint_artifact is not None else None,
            "test_artifact_id": str(test_artifact.id) if test_artifact is not None else None,
        }
        review.diff_artifact_id = diff_artifact.id
        review.metadata_json = metadata

        if reviewer_session is not None:
            reviewer_session_metadata = dict(reviewer_session.metadata_json)
            reviewer_session_metadata["review_input"] = review_packet
            reviewer_session.metadata_json = reviewer_session_metadata

        self.db.commit()
        self.db.refresh(review)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.requested",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.create"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.requester_session_id,
                payload={
                    "review_id": str(review.id),
                    "status": review.status.value,
                },
            )
        )
        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.create"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.requester_session_id,
                payload={
                    "review_id": str(review.id),
                    "status": review.status.value,
                    "changed_files": diff.changed_files,
                    "lint_requested": lint_result is not None,
                    "tests_requested": test_result is not None,
                },
            )
        )
        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.input_packaged",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", id="review-service"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.reviewer_session_id,
                payload={
                    "review_id": str(review.id),
                    "reviewer_session_id": str(review.reviewer_session_id) if review.reviewer_session_id else None,
                    "prompt_template_path": str(self.reviewer_prompt_path),
                },
            )
        )
        logger.info(
            "created review %s for task=%s worker_session=%s reviewer_session=%s",
            review.id,
            review.task_id,
            worker_session.id,
            review.reviewer_session_id,
        )
        return self._build_review_read(review)

    def approve_review(self, review_id: UUID, payload: ReviewApprove) -> ReviewRead:
        review = self._get_review_record(review_id)
        if payload.reviewer_session_id is not None:
            reviewer_session = self._resolve_reviewer_session(review.project_id, payload.reviewer_session_id)
            review.reviewer_session_id = reviewer_session.id
        review.status = ReviewStatus.APPROVED
        review.decision_note = payload.note
        metadata = dict(review.metadata_json)
        metadata["reviewer_notes"] = payload.note
        review.metadata_json = metadata
        self.db.commit()
        self.db.refresh(review)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.approved",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.approve"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.reviewer_session_id,
                payload={"review_id": str(review.id), "note": payload.note},
            )
        )
        logger.info("review %s approved", review.id)
        return self._build_review_read(review)

    def reject_review(self, review_id: UUID, payload: ReviewReject) -> ReviewRead:
        review = self._get_review_record(review_id)
        if payload.status not in {ReviewStatus.NEEDS_CHANGES, ReviewStatus.REJECTED}:
            raise ValidationError("Reject reviews must end in needs_changes or rejected.")
        if payload.reviewer_session_id is not None:
            reviewer_session = self._resolve_reviewer_session(review.project_id, payload.reviewer_session_id)
            review.reviewer_session_id = reviewer_session.id
        review.status = payload.status
        review.decision_note = payload.note
        metadata = dict(review.metadata_json)
        metadata["reviewer_notes"] = payload.note
        review.metadata_json = metadata
        self.db.commit()
        self.db.refresh(review)

        event_type = "review.needs_changes" if payload.status == ReviewStatus.NEEDS_CHANGES else "review.rejected"
        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type=event_type,
                level=EventLevel.WARN,
                source=EventSourceRef(kind="api", id="reviews.reject"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.reviewer_session_id,
                payload={"review_id": str(review.id), "note": payload.note},
            )
        )
        logger.info("review %s moved to %s", review.id, payload.status.value)
        return self._build_review_read(review)

    def mark_merge_ready(self, review_id: UUID, payload: ReviewMergeReady) -> ReviewRead:
        review = self._get_review_record(review_id)
        if review.status != ReviewStatus.APPROVED:
            raise ValidationError("Reviewer approval is required before a review can be marked merge-ready.")

        now = datetime.now(UTC)
        review.human_approved_by = payload.approved_by
        review.human_approved_at = now
        metadata = dict(review.metadata_json)
        approval = dict(metadata.get("approval", {}))
        approval.update(
            {
                "merge_ready": True,
                "merge_ready_by": payload.approved_by,
                "merge_ready_at": now.isoformat(),
                "note": payload.note,
            }
        )
        metadata["approval"] = approval
        review.metadata_json = metadata

        task = self.db.get(Task, review.task_id)
        if task is not None:
            task.status = TaskStatus.DONE
            task_metadata = dict(task.metadata_json)
            task_metadata["review"] = {
                "merge_ready": True,
                "review_id": str(review.id),
                "approved_by": payload.approved_by,
                "approved_at": now.isoformat(),
            }
            task.metadata_json = task_metadata

        self.db.commit()
        self.db.refresh(review)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.merge_ready",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.merge-ready"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.reviewer_session_id,
                payload={"review_id": str(review.id), "approved_by": payload.approved_by, "note": payload.note},
            )
        )
        logger.info("review %s marked merge-ready by %s", review.id, payload.approved_by)
        return self._build_review_read(review)

    def _resolve_task(self, payload: ReviewCreate) -> Task:
        if payload.task_id is not None:
            task = self.db.get(Task, payload.task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {payload.task_id}")
            if task.project_id != payload.project_id:
                raise ValidationError("Task must belong to the same project.")
            return task
        if payload.session_id is None:
            raise ValidationError("Either task_id or session_id must be provided.")
        session = self.db.get(SessionModel, payload.session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {payload.session_id}")
        if session.task_id is None:
            raise ValidationError("Review sessions must resolve to a task.")
        task = self.db.get(Task, session.task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {session.task_id}")
        return task

    def _resolve_worker_session(self, *, task: Task, session_id: UUID | None) -> SessionModel:
        if session_id is not None:
            session = self.db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
        else:
            assigned_session_id = task.metadata_json.get("orchestrator", {}).get("assigned_session_id")
            session = self.db.get(SessionModel, UUID(assigned_session_id)) if assigned_session_id else None
            if session is None:
                session = self.db.scalar(
                    select(SessionModel)
                    .where(SessionModel.task_id == task.id, SessionModel.role == SessionRole.WORKER)
                    .order_by(SessionModel.created_at.desc())
                )
        if session is None:
            raise NotFoundError(f"No worker session found for task: {task.id}")
        if session.project_id != task.project_id or session.role != SessionRole.WORKER:
            raise ValidationError("Review source session must be a worker session in the same project.")
        return session

    def _resolve_reviewer_session(
        self,
        project_id: UUID,
        reviewer_session_id: UUID | None,
    ) -> SessionModel | None:
        if reviewer_session_id is None:
            return None
        session = self.db.get(SessionModel, reviewer_session_id)
        if session is None:
            raise NotFoundError(f"Reviewer session not found: {reviewer_session_id}")
        if session.project_id != project_id or session.role != SessionRole.REVIEWER:
            raise ValidationError("Reviewer session must belong to the same project and use reviewer role.")
        return session

    def _resolve_workspace(self, session_id: UUID) -> Workspace:
        workspace = self.db.scalar(select(Workspace).where(Workspace.session_id == session_id))
        if workspace is None:
            raise NotFoundError(f"Workspace not found for session: {session_id}")
        return workspace

    def _run_optional_check(
        self,
        workspace_id: UUID,
        *,
        kind: str,
        command: str | None,
    ) -> dict | None:
        if command is None or not command.strip():
            return None
        request = WorkspaceCommandRequest(command=command)
        result = (
            self.workspace_service.run_lint(workspace_id, request)
            if kind == "lint"
            else self.workspace_service.run_tests(workspace_id, request)
        )
        return {
            "kind": kind,
            "command": result.command,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "changed_files": result.changed_files,
            "status": self._command_status(result),
            "summary": self._command_summary(result),
        }

    @staticmethod
    def _command_status(result: WorkspaceCommandRead) -> str:
        if result.timed_out:
            return "timed_out"
        return "passed" if result.returncode == 0 else "failed"

    @staticmethod
    def _command_summary(result: WorkspaceCommandRead) -> str:
        if result.timed_out:
            return f"{result.kind} timed out after {result.duration_ms}ms."
        return (
            f"{result.kind} passed."
            if result.returncode == 0
            else f"{result.kind} failed with exit code {result.returncode}."
        )

    def _create_diff_artifact(
        self,
        review: Review,
        diff: WorkspaceDiffRead,
        diff_summary: dict,
    ) -> Artifact:
        return self._create_artifact(
            project_id=review.project_id,
            task_id=review.task_id,
            session_id=review.requester_session_id,
            kind=ArtifactKind.DIFF,
            uri=f"inline://reviews/{review.id}/diff",
            content_type="text/x-diff",
            metadata={
                "diff": diff.diff,
                "summary": diff_summary,
                "changed_files": diff.changed_files,
                "workspace_id": str(diff.workspace_id),
            },
        )

    def _create_check_artifact(
        self,
        review: Review,
        result: dict | None,
        *,
        kind: ArtifactKind,
    ) -> Artifact | None:
        if result is None:
            return None
        return self._create_artifact(
            project_id=review.project_id,
            task_id=review.task_id,
            session_id=review.requester_session_id,
            kind=kind,
            uri=f"inline://reviews/{review.id}/{kind.value}",
            content_type="application/json",
            metadata=result,
        )

    def _create_artifact(
        self,
        *,
        project_id: UUID,
        task_id: UUID,
        session_id: UUID | None,
        kind: ArtifactKind,
        uri: str,
        content_type: str,
        metadata: dict,
    ) -> Artifact:
        rendered = str(metadata)
        artifact = Artifact(
            project_id=project_id,
            task_id=task_id,
            session_id=session_id,
            kind=kind,
            uri=uri,
            content_type=content_type,
            size_bytes=len(rendered.encode("utf-8")),
            metadata_json=metadata,
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        return artifact

    def _build_review_packet(
        self,
        *,
        review: Review,
        task: Task,
        worker_session: SessionModel,
        reviewer_session: SessionModel | None,
        workspace: Workspace,
        diff: WorkspaceDiffRead,
        diff_summary: dict,
        lint_result: dict | None,
        test_result: dict | None,
        prompt_text: str,
    ) -> dict:
        return {
            "review_id": str(review.id),
            "project_id": str(review.project_id),
            "task": {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "acceptance_criteria": task.acceptance_criteria,
            },
            "worker_session": {
                "id": str(worker_session.id),
                "branch_name": worker_session.branch_name,
                "workspace_path": worker_session.workspace_path,
            },
            "reviewer_session": str(reviewer_session.id) if reviewer_session is not None else None,
            "workspace": {
                "id": str(workspace.id),
                "branch_name": workspace.branch_name,
                "base_branch": workspace.base_branch,
                "base_commit": workspace.base_commit,
                "head_commit": workspace.head_commit,
            },
            "diff": {
                "summary": diff_summary,
                "changed_files": diff.changed_files,
                "diff": diff.diff,
            },
            "lint": lint_result,
            "tests": test_result,
            "prompt_template_path": str(self.reviewer_prompt_path),
            "prompt_template": prompt_text,
        }

    def _summarize_diff(self, diff: WorkspaceDiffRead) -> dict:
        changed_files = diff.changed_files
        return {
            "summary": f"{len(changed_files)} changed file(s)",
            "file_count": len(changed_files),
            "changed_files": changed_files,
            "diff_preview": diff.diff[:4000],
        }

    def _build_review_read(self, review: Review) -> ReviewRead:
        metadata = dict(review.metadata_json)
        artifact_ids = [
            review.diff_artifact_id,
            self._uuid_from_metadata(metadata, "artifacts", "lint_artifact_id"),
            self._uuid_from_metadata(metadata, "artifacts", "test_artifact_id"),
        ]
        artifacts = [
            artifact
            for artifact in (
                self.db.get(Artifact, artifact_id) if artifact_id is not None else None
                for artifact_id in artifact_ids
            )
            if artifact is not None
        ]
        artifact_reads = [
            ReviewArtifactRead(
                id=artifact.id,
                kind=artifact.kind,
                uri=artifact.uri,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                metadata=artifact.metadata_json,
            )
            for artifact in artifacts
        ]
        diff_artifact = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.DIFF), None)
        lint_artifact = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.LINT_REPORT), None)
        test_artifact = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.TEST_REPORT), None)

        approval_metadata = dict(metadata.get("approval", {}))
        return ReviewRead(
            id=review.id,
            project_id=review.project_id,
            task_id=review.task_id,
            requester_session_id=review.requester_session_id,
            reviewer_session_id=review.reviewer_session_id,
            status=review.status,
            summary=review.summary,
            reviewer_notes=review.decision_note or metadata.get("reviewer_notes"),
            prompt_template_path=metadata.get("prompt_template_path"),
            review_packet=metadata.get("review_packet", {}),
            diff=ReviewDiffSummaryRead(
                artifact_id=diff_artifact.id if diff_artifact is not None else None,
                summary=metadata.get("diff_summary", {}).get("summary", "No diff summary available."),
                changed_files=metadata.get("changed_files", []),
                diff_preview=metadata.get("diff_summary", {}).get("diff_preview", ""),
            ),
            lint=self._build_check_read(lint_artifact),
            tests=self._build_check_read(test_artifact),
            approval=ReviewApprovalRead(
                reviewer_status=review.status,
                human_approved=review.human_approved_by is not None,
                human_approved_by=review.human_approved_by,
                human_approved_at=review.human_approved_at,
                merge_ready=bool(approval_metadata.get("merge_ready")),
                merge_ready_by=approval_metadata.get("merge_ready_by"),
                merge_ready_at=self._parse_datetime(approval_metadata.get("merge_ready_at")),
                note=approval_metadata.get("note"),
            ),
            artifacts=artifact_reads,
            metadata=metadata,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )

    @staticmethod
    def _build_check_read(artifact: Artifact | None) -> ReviewCheckRead | None:
        if artifact is None:
            return None
        metadata = artifact.metadata_json
        return ReviewCheckRead(
            artifact_id=artifact.id,
            command=metadata.get("command"),
            status=metadata.get("status"),
            returncode=metadata.get("returncode"),
            timed_out=bool(metadata.get("timed_out", False)),
            duration_ms=metadata.get("duration_ms"),
            summary=metadata.get("summary"),
        )

    @staticmethod
    def _uuid_from_metadata(metadata: dict, outer_key: str, inner_key: str) -> UUID | None:
        raw_value = metadata.get(outer_key, {}).get(inner_key)
        return UUID(raw_value) if raw_value else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _get_review_record(self, review_id: UUID) -> Review:
        review = self.db.get(Review, review_id)
        if review is None:
            raise NotFoundError(f"Review not found: {review_id}")
        return review
