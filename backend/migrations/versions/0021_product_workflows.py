"""Member collaboration, private event feeds and product workflows."""

from pathlib import Path

from alembic import op

revision = "0021"
down_revision = "0020"


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
