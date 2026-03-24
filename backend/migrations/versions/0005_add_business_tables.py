"""add business and business_cycle tables for autonomous business agent"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_add_business_tables"
down_revision: str | None = "0004_project_decomposition"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("portfolio_id", sa.Uuid(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), unique=True, nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("business_type", sa.String(30), server_default="saas", nullable=False),
        sa.Column("domain", sa.String(253), nullable=True),
        sa.Column("infra_state", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("budget_monthly_usd", sa.Numeric(10, 2), server_default="0.00", nullable=False),
        sa.Column("total_revenue_usd", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("daily_cycle_cron", sa.String(50), server_default="0 8 * * *", nullable=False),
        sa.Column("active_agent_types", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("status", sa.String(20), server_default="setup", nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_businesses_portfolio_id", "businesses", ["portfolio_id"])
    op.create_index("ix_businesses_slug", "businesses", ["slug"], unique=True)
    op.create_index("ix_businesses_status", "businesses", ["status"])

    op.create_table(
        "business_cycles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("ceo_session_id", sa.Uuid(), nullable=True),
        sa.Column("ceo_plan", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("agent_results", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("metrics_before", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("metrics_after", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("human_feedback", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_business_cycles_business_id", "business_cycles", ["business_id"])
    op.create_index(
        "ix_business_cycles_business_date",
        "business_cycles",
        ["business_id", "cycle_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("business_cycles")
    op.drop_table("businesses")
