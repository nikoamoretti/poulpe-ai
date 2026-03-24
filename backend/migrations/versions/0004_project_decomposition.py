"""add parent_project_id for project decomposition"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_project_decomposition"
down_revision: str | None = "0003_project_checkpoints"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("parent_project_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_parent_project_id",
        "projects",
        "projects",
        ["parent_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_projects_parent_project_id", "projects", ["parent_project_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_parent_project_id", table_name="projects")
    op.drop_constraint("fk_projects_parent_project_id", "projects", type_="foreignkey")
    op.drop_column("projects", "parent_project_id")
