"""portfolio phase 2 foundation"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_portfolio_phase2"
down_revision: str | None = "0001_backend_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Enum("active", "archived", name="project_status", native_enum=False), nullable=False),
        sa.Column("manager_session_id", sa.Uuid(), nullable=True),
        sa.Column("manager_workspace_path", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portfolios_slug", "portfolios", ["slug"], unique=True)

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("portfolio_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("objective", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("worker_session_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("completion_summary", sa.Text(), nullable=True))
        batch_op.create_index("ix_projects_portfolio_id", ["portfolio_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_projects_portfolio_id_portfolios",
            "portfolios",
            ["portfolio_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("portfolio_id", sa.Uuid(), nullable=True))
        batch_op.alter_column("project_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.create_index("ix_sessions_portfolio_id", ["portfolio_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_sessions_portfolio_id_portfolios",
            "portfolios",
            ["portfolio_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_portfolio_id_portfolios", type_="foreignkey")
        batch_op.drop_index("ix_sessions_portfolio_id")
        batch_op.alter_column("project_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column("portfolio_id")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("fk_projects_portfolio_id_portfolios", type_="foreignkey")
        batch_op.drop_index("ix_projects_portfolio_id")
        batch_op.drop_column("completion_summary")
        batch_op.drop_column("worker_session_id")
        batch_op.drop_column("objective")
        batch_op.drop_column("portfolio_id")

    op.drop_index("ix_portfolios_slug", table_name="portfolios")
    op.drop_table("portfolios")
