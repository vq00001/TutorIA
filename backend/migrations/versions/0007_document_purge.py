"""Withdraw immediately; purge source lineage using a dedicated technical function."""

from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
