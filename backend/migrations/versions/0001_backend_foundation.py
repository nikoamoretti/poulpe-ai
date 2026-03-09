"""backend foundation"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_backend_foundation"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    project_status = sa.Enum("active", "archived", name="project_status", native_enum=False)
    task_status = sa.Enum(
        "pending",
        "in_progress",
        "blocked",
        "review",
        "done",
        "canceled",
        name="task_status",
        native_enum=False,
    )
    session_role = sa.Enum("manager", "worker", "reviewer", name="session_role", native_enum=False)
    session_status = sa.Enum(
        "pending",
        "starting",
        "running",
        "blocked",
        "completed",
        "failed",
        "stopped",
        name="session_status",
        native_enum=False,
    )
    session_transport = sa.Enum("local_process", name="session_transport", native_enum=False)
    workspace_status = sa.Enum(
        "planned",
        "provisioning",
        "ready",
        "dirty",
        "archived",
        "failed",
        name="workspace_status",
        native_enum=False,
    )
    artifact_kind = sa.Enum(
        "diff",
        "patch",
        "test_report",
        "lint_report",
        "session_log",
        "transcript",
        "bundle",
        "note",
        name="artifact_kind",
        native_enum=False,
    )
    transcript_stream = sa.Enum(
        "stdin",
        "stdout",
        "stderr",
        "system",
        name="transcript_stream",
        native_enum=False,
    )
    structured_event_type = sa.Enum(
        "start",
        "progress",
        "question",
        "blocked",
        "tests_run",
        "complete",
        "error",
        "heartbeat",
        name="structured_event_type",
        native_enum=False,
    )
    structured_event_status = sa.Enum(
        "valid",
        "malformed",
        name="structured_event_status",
        native_enum=False,
    )
    review_status = sa.Enum(
        "pending",
        "running",
        "needs_changes",
        "approved",
        "rejected",
        name="review_status",
        native_enum=False,
    )
    event_category = sa.Enum(
        "project",
        "task",
        "session",
        "event",
        "review",
        "artifact",
        "workspace",
        "system",
        name="event_category",
        native_enum=False,
    )
    event_level = sa.Enum("debug", "info", "warn", "error", name="event_level", native_enum=False)

    bind = op.get_bind()
    for enum in (
        project_status,
        task_status,
        session_role,
        session_status,
        session_transport,
        workspace_status,
        artifact_kind,
        transcript_stream,
        structured_event_type,
        structured_event_status,
        review_status,
        event_category,
        event_level,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=120), nullable=False),
        sa.Column("status", project_status, nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"], unique=False)
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("supervisor_session_id", sa.Uuid(), nullable=True),
        sa.Column("role", session_role, nullable=False),
        sa.Column("status", session_status, nullable=False),
        sa.Column("transport", session_transport, nullable=False),
        sa.Column("adapter_kind", sa.String(length=120), nullable=False),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("runtime_metadata", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supervisor_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"], unique=False)
    op.create_index("ix_sessions_task_id", "sessions", ["task_id"], unique=False)

    op.create_table(
        "transcript_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stream", transcript_stream, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transcript_chunks_session_id", "transcript_chunks", ["session_id"], unique=False)

    op.create_table(
        "parsed_session_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("transcript_sequence", sa.Integer(), nullable=True),
        sa.Column("stream", transcript_stream, nullable=False),
        sa.Column("status", structured_event_status, nullable=False),
        sa.Column("event_type", structured_event_type, nullable=True),
        sa.Column("declared_type", sa.String(length=120), nullable=True),
        sa.Column("level", event_level, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("raw_block", sa.Text(), nullable=False),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parsed_session_events_session_id",
        "parsed_session_events",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("branch_name", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("head_commit", sa.String(length=64), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("status", workspace_status, nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_workspaces_project_id", "workspaces", ["project_id"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", artifact_kind, nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("requester_session_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_session_id", sa.Uuid(), nullable=True),
        sa.Column("diff_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("status", review_status, nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("lint_status", sa.String(length=32), nullable=True),
        sa.Column("test_status", sa.String(length=32), nullable=True),
        sa.Column("human_approved_by", sa.String(length=120), nullable=True),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["diff_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reviews_project_id", "reviews", ["project_id"], unique=False)
    op.create_index("ix_reviews_task_id", "reviews", ["task_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("category", event_category, nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("level", event_level, nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_project_id", "events", ["project_id"], unique=False)
    op.create_index("ix_events_task_id", "events", ["task_id"], unique=False)
    op.create_index("ix_events_session_id", "events", ["session_id"], unique=False)
    op.create_index("ix_events_sequence", "events", ["sequence"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_parsed_session_events_session_id", table_name="parsed_session_events")
    op.drop_table("parsed_session_events")

    op.drop_index("ix_events_sequence", table_name="events")
    op.drop_index("ix_events_session_id", table_name="events")
    op.drop_index("ix_events_task_id", table_name="events")
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_reviews_task_id", table_name="reviews")
    op.drop_index("ix_reviews_project_id", table_name="reviews")
    op.drop_table("reviews")

    op.drop_table("artifacts")

    op.drop_index("ix_workspaces_project_id", table_name="workspaces")
    op.drop_table("workspaces")

    op.drop_index("ix_transcript_chunks_session_id", table_name="transcript_chunks")
    op.drop_table("transcript_chunks")

    op.drop_index("ix_sessions_task_id", table_name="sessions")
    op.drop_index("ix_sessions_project_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_table("projects")

    bind = op.get_bind()
    for enum_name in (
        "event_level",
        "event_category",
        "review_status",
        "artifact_kind",
        "workspace_status",
        "session_transport",
        "session_status",
        "session_role",
        "task_status",
        "project_status",
        "structured_event_status",
        "structured_event_type",
        "transcript_stream",
    ):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
