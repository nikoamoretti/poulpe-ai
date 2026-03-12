"""project checkpoints for portfolio supervision"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_project_checkpoints"
down_revision: str | None = "0002_portfolio_phase2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_session_id", sa.Uuid(), nullable=True),
        sa.Column("manager_session_id", sa.Uuid(), nullable=True),
        sa.Column("source_parsed_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("question", "blocked", "completion", "error", name="project_checkpoint_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", "dismissed", name="project_checkpoint_status", native_enum=False),
            nullable=False,
            server_default="open",
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "resolution",
            sa.Enum(
                "answered",
                "approved",
                "changes_requested",
                "dismissed",
                name="project_checkpoint_resolution",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("response_details", sa.JSON(), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["manager_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_parsed_event_id"], ["parsed_session_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_parsed_event_id"),
    )
    op.create_index("ix_project_checkpoints_portfolio_id", "project_checkpoints", ["portfolio_id"], unique=False)
    op.create_index("ix_project_checkpoints_project_id", "project_checkpoints", ["project_id"], unique=False)
    op.create_index(
        "ix_project_checkpoints_source_session_id",
        "project_checkpoints",
        ["source_session_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_checkpoints_manager_session_id",
        "project_checkpoints",
        ["manager_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_project_checkpoints_manager_session_id", table_name="project_checkpoints")
    op.drop_index("ix_project_checkpoints_source_session_id", table_name="project_checkpoints")
    op.drop_index("ix_project_checkpoints_project_id", table_name="project_checkpoints")
    op.drop_index("ix_project_checkpoints_portfolio_id", table_name="project_checkpoints")
    op.drop_table("project_checkpoints")
