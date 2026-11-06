"""Publication, graph cycles, attempts and state machines."""

from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead")
