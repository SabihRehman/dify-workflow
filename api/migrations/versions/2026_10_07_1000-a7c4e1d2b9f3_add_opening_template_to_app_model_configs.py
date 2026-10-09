"""add opening_template and opening_template_enabled to app_model_configs

Revision ID: a7c4e1d2b9f3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-07 10:00:00.000000

"""
import models.types
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c4e1d2b9f3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("app_model_configs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("opening_template", models.types.LongText(), nullable=True))
        batch_op.add_column(sa.Column("opening_template_enabled", sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table("app_model_configs", schema=None) as batch_op:
        batch_op.drop_column("opening_template_enabled")
        batch_op.drop_column("opening_template")
