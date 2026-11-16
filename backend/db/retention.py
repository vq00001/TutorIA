"""Mantenimiento periódico del esquema actual."""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .account_tokens import account_tokens
from .audit import audit_events
from .courses import courses, memberships
from .documents import documents
from .generations import generation_sources
from .idempotency import idempotency_requests
from .jobs import jobs
from .processing import chunk_embeddings, processing_runs
from .purge import delete_private_data, expire_receipts
from .quotas import upload_usage
from .sessions import sessions

maintenance_runs = sa.Table(
    "maintenance_runs", jobs.metadata,
    sa.Column("bucket", sa.Date, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

_jobs_finished = sa.Index(
    "jobs_finished_idx", jobs.c.finished_at,
    postgresql_where=jobs.c.finished_at.is_not(None),
)


def install_retention(conn: Connection) -> None:
    maintenance_runs.create(conn, checkfirst=True)
    conn.exec_driver_sql(
        "REVOKE ALL ON app.maintenance_runs FROM PUBLIC, tutoria_api, tutoria_worker"
    )
    conn.exec_driver_sql("ALTER TABLE app.jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.stamp_finished_job() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            IF NEW.state IN ('succeeded', 'failed') THEN
                NEW.finished_at := coalesce(NEW.finished_at, clock_timestamp());
            ELSE
                NEW.finished_at := NULL;
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS stamp_finished_job ON app.jobs")
    conn.exec_driver_sql(
        """CREATE TRIGGER stamp_finished_job BEFORE INSERT OR UPDATE OF state ON app.jobs
        FOR EACH ROW EXECUTE FUNCTION app.stamp_finished_job()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.stamp_finished_job() FROM PUBLIC")
    conn.execute(jobs.update().where(
        jobs.c.state.in_(("succeeded", "failed")), jobs.c.finished_at.is_(None)
    ).values(finished_at=sa.func.clock_timestamp()))
    _jobs_finished.create(conn, checkfirst=True)


def _expire_processing(conn: Connection, at: datetime) -> None:
    run = processing_runs.c
    doc = documents.c
    source = generation_sources.c
    eligible_age = sa.or_(
        sa.and_(run.state == "failed", run.finished_at <= at - timedelta(days=1)),
        sa.and_(run.state == "ready", run.finished_at <= at - timedelta(days=30)),
    )
    candidates = conn.execute(
        sa.select(run.id, run.course_id, run.document_id)
        .join(documents, doc.id == run.document_id)
        .where(
            eligible_age, doc.active_run_id.is_distinct_from(run.id),
            ~sa.exists(sa.select(1).where(source.run_id == run.id).correlate(processing_runs)),
        )
        .order_by(run.course_id, run.document_id, run.id)
        .limit(100)
    ).all()
    for row in candidates:
        conn.execute(sa.select(courses.c.id).where(courses.c.id == row.course_id)
                     .with_for_update()).all()
        document = conn.execute(sa.select(doc.active_run_id).where(doc.id == row.document_id)
                                .with_for_update()).one_or_none()
        if document is None or document.active_run_id == row.id:
            continue
        conn.execute(sa.select(chunk_embeddings.c.child_id).where(
            chunk_embeddings.c.run_id == row.id
        ).with_for_update()).all()
        conn.execute(processing_runs.delete().where(
            run.id == row.id, eligible_age,
            ~sa.exists(sa.select(1).where(source.run_id == run.id).correlate(processing_runs)),
        ))


def _expire_membership_data(conn: Connection, at: datetime) -> None:
    member = memberships.c
    eligible = sa.and_(
        member.status.in_(("left", "removed")), member.left_at <= at - timedelta(days=30)
    )
    candidates = conn.execute(
        sa.select(member.course_id, member.user_id).where(eligible)
        .order_by(member.course_id, member.user_id)
    ).all()
    for course_id, user_id in candidates:
        conn.execute(sa.select(courses.c.id).where(courses.c.id == course_id)
                     .with_for_update()).all()
        still_eligible = conn.execute(sa.select(member.user_id).where(
            member.course_id == course_id, member.user_id == user_id, eligible
        ).with_for_update()).one_or_none()
        if still_eligible is not None:
            delete_private_data(conn, course_id=course_id, user_id=user_id)


def run_retention(conn: Connection, *, at: datetime) -> bool:
    """Ejecuta una vez por día UTC. La marca y la limpieza se confirman juntas."""
    if at.utcoffset() is None:
        raise ValueError("RETENTION_TIMESTAMP_REQUIRES_TIMEZONE")
    bucket = at.astimezone(timezone.utc).date()
    with conn.begin_nested():
        claimed = conn.scalar(insert(maintenance_runs).values(
            bucket=bucket, created_at=at
        ).on_conflict_do_nothing().returning(maintenance_runs.c.bucket))
        if claimed is None:
            return False

        _expire_processing(conn, at)
        _expire_membership_data(conn, at)
        day_ago = at - timedelta(days=1)
        expiration = (
            (sessions, sessions.c.expires_at <= day_ago),
            (account_tokens, sa.func.coalesce(account_tokens.c.consumed_at,
                                              account_tokens.c.expires_at) <= day_ago),
            (upload_usage, upload_usage.c.accepted_at <= day_ago),
            (idempotency_requests, idempotency_requests.c.expires_at <= at),
            (jobs, sa.and_(jobs.c.state.in_(("succeeded", "failed")),
                           jobs.c.finished_at <= at - timedelta(days=7))),
            (audit_events, audit_events.c.created_at <= at - timedelta(days=90)),
            (maintenance_runs, maintenance_runs.c.bucket < bucket - timedelta(days=90)),
        )
        for table, predicate in expiration:
            conn.execute(table.delete().where(predicate))
        expire_receipts(conn, at)
        return True
