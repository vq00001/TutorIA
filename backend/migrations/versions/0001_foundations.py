"""Identity, courses and SQL security boundary."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade():
    raise RuntimeError("Destructive downgrade requires an explicit backup/restore procedure")
