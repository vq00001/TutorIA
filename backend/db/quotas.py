"""Cuotas de almacenamiento, carga diaria y generaciones activas del ZIP."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .documents import document_versions
from .generations import generation_runs

metadata = document_versions.metadata
quota_limits = sa.Table(
    "quota_limits",
    metadata,
    sa.Column("version", sa.Text, primary_key=True),
    sa.Column("course_bytes", sa.BigInteger, nullable=False),
    sa.Column("user_daily_bytes", sa.BigInteger, nullable=False),
    sa.Column("max_active_generations", sa.Integer, nullable=False),
    sa.CheckConstraint(
        "course_bytes > 0 AND user_daily_bytes > 0 AND max_active_generations > 0",
        name="quota_limits_positive",
    ),
)
upload_usage = sa.Table(
    "upload_usage",
    metadata,
    # El identificador se conserva aunque se borre la versión.
    sa.Column("version_id", sa.Uuid, primary_key=True),
    sa.Column(
        "user_id", sa.Uuid, sa.ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("size_bytes > 0", name="upload_usage_positive"),
)
sa.Index("upload_usage_user_time_idx", upload_usage.c.user_id, upload_usage.c.accepted_at)


def _quota_lock_table(name: str, key: str, target: str) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        sa.Column(key, sa.Uuid, sa.ForeignKey(target, ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    )


_user_locks = _quota_lock_table("quota_user_locks", "user_id", "app.users.id")
_course_locks = _quota_lock_table("quota_course_locks", "course_id", "app.courses.id")
_active_generations = sa.Index(
    "generation_runs_active_user_idx",
    generation_runs.c.user_id,
    postgresql_where=generation_runs.c.status.in_(("queued", "running")),
)


def install_quotas(conn: Connection) -> None:
    for table in (quota_limits, upload_usage, _user_locks, _course_locks):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(
            f"REVOKE ALL ON app.{table.name} FROM PUBLIC, tutoria_api, tutoria_worker"
        )
    _active_generations.create(conn, checkfirst=True)
    conn.execute(
        insert(quota_limits)
        .values(
            version="v1",
            course_bytes=2_000_000_000,
            user_daily_bytes=1_000_000_000,
            max_active_generations=2,
        )
        .on_conflict_do_nothing(index_elements=[quota_limits.c.version])
    )
    conn.execute(
        insert(upload_usage)
        .from_select(
            ["version_id", "user_id", "size_bytes", "accepted_at"],
            sa.select(
                document_versions.c.id,
                document_versions.c.uploaded_by,
                document_versions.c.size_bytes,
                document_versions.c.created_at,
            ).where(
                document_versions.c.created_at > sa.func.now() - sa.text("interval '24 hours'")
            ),
        )
        .on_conflict_do_nothing(index_elements=[upload_usage.c.version_id])
    )
    _install_upload_quota(conn)
    _install_generation_quota(conn)


def _install_upload_quota(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.reserve_upload_quota() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            limits app.quota_limits;
            accepted timestamptz;
            stored_bytes numeric;
            daily_bytes numeric;
        BEGIN
            -- Las filas de control serializan las reservas entre usuarios y clases.
            INSERT INTO app.quota_user_locks AS guard (user_id) VALUES (NEW.uploaded_by)
            ON CONFLICT (user_id) DO UPDATE SET revision = guard.revision + 1;
            INSERT INTO app.quota_course_locks AS guard (course_id) VALUES (NEW.course_id)
            ON CONFLICT (course_id) DO UPDATE SET revision = guard.revision + 1;

            accepted := clock_timestamp();
            SELECT * INTO STRICT limits FROM app.quota_limits WHERE version = 'v1';
            SELECT coalesce(sum(size_bytes), 0) INTO stored_bytes
            FROM app.document_versions WHERE course_id = NEW.course_id AND status <> 'purged' AND id <> NEW.id;
            SELECT coalesce(sum(size_bytes), 0) INTO daily_bytes
            FROM app.upload_usage
            WHERE user_id = NEW.uploaded_by AND accepted_at > accepted - interval '24 hours';

            IF stored_bytes + NEW.size_bytes > limits.course_bytes
                OR daily_bytes + NEW.size_bytes > limits.user_daily_bytes THEN
                RAISE EXCEPTION 'UPLOAD_QUOTA_EXCEEDED' USING ERRCODE = '23514';
            END IF;
            INSERT INTO app.upload_usage (version_id, user_id, size_bytes, accepted_at)
            VALUES (NEW.id, NEW.uploaded_by, NEW.size_bytes, accepted);
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.reserve_upload_quota() FROM PUBLIC")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS reserve_upload_quota ON app.document_versions")
    conn.exec_driver_sql(
        """CREATE TRIGGER reserve_upload_quota AFTER INSERT ON app.document_versions
        FOR EACH ROW EXECUTE FUNCTION app.reserve_upload_quota()"""
    )


def _install_generation_quota(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.reserve_generation_quota() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            maximum integer;
            active_count bigint;
        BEGIN
            IF NEW.status NOT IN ('queued', 'running') THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status IN ('queued', 'running') THEN
                RETURN NEW;
            END IF;
            INSERT INTO app.quota_user_locks AS guard (user_id) VALUES (NEW.user_id)
            ON CONFLICT (user_id) DO UPDATE SET revision = guard.revision + 1;
            SELECT max_active_generations INTO STRICT maximum
            FROM app.quota_limits WHERE version = 'v1';
            SELECT count(*) INTO active_count FROM app.generation_runs
            WHERE user_id = NEW.user_id AND status IN ('queued', 'running');

            IF active_count >= maximum THEN
                RAISE EXCEPTION 'GENERATION_QUOTA_EXCEEDED' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.reserve_generation_quota() FROM PUBLIC")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS reserve_generation_quota ON app.generation_runs")
    conn.exec_driver_sql(
        """CREATE TRIGGER reserve_generation_quota BEFORE INSERT OR UPDATE OF status
        ON app.generation_runs FOR EACH ROW EXECUTE FUNCTION app.reserve_generation_quota()"""
    )
