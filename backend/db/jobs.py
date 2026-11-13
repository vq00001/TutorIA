"""Cola PostgreSQL con reservas y reintentos, adaptada del ZIP."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Connection, RowMapping

from .generations import generation_runs

JobKind = Literal["ingest_document", "generate_chat", "generate_exam"]
jobs = sa.Table(
    "jobs",
    generation_runs.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("requested_by", sa.Uuid, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("target_id", sa.Uuid, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("idempotency_key", sa.String(128), nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="queued"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False),
    sa.Column("scheduled_at", sa.DateTime(timezone=True)),
    sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("lease_token", sa.Uuid),
    sa.Column("locked_by", sa.String(128)),
    sa.Column("lease_until", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.String(80)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "requested_by"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("course_id", "requested_by", "kind", "idempotency_key"),
    sa.CheckConstraint(
        "kind IN ('ingest_document', 'generate_chat', 'generate_exam') AND jsonb_typeof(payload) = 'object'",
        name="jobs_kind_payload",
    ),
    sa.CheckConstraint(
        "state IN ('queued', 'running', 'succeeded', 'failed') "
        "AND max_attempts BETWEEN 1 AND 3 AND attempts BETWEEN 0 AND max_attempts",
        name="jobs_state_attempts",
    ),
    sa.CheckConstraint(
        "(state = 'running' AND lease_token IS NOT NULL AND locked_by IS NOT NULL "
        "AND lease_until IS NOT NULL AND lease_until <= deadline_at) OR "
        "(state <> 'running' AND lease_token IS NULL AND locked_by IS NULL AND lease_until IS NULL)",
        name="jobs_lease",
    ),
)
sa.Index(
    "jobs_generation_target_idx",
    jobs.c.target_id,
    unique=True,
    postgresql_where=jobs.c.kind.in_(("generate_chat", "generate_exam")),
)
sa.Index(
    "jobs_ready_idx",
    jobs.c.run_after,
    jobs.c.created_at,
    jobs.c.id,
    postgresql_where=jobs.c.state == "queued",
)
sa.Index(
    "jobs_active_deadline_idx",
    jobs.c.deadline_at,
    postgresql_where=jobs.c.state.in_(("queued", "running")),
)
sa.Index(
    "jobs_lease_expiry_idx",
    jobs.c.lease_until,
    postgresql_where=jobs.c.state == "running",
)


def enqueue_job(
    conn: Connection,
    course_id: UUID,
    *,
    kind: JobKind,
    target_id: UUID,
    idempotency_key: str,
    deadline_at: datetime,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> UUID:
    return conn.execute(
        sa.select(
            sa.func.app.enqueue_job(
                course_id,
                kind,
                target_id,
                idempotency_key,
                sa.cast({} if payload is None else payload, JSONB),
                sa.cast(run_after, sa.DateTime(timezone=True)),
                sa.cast(deadline_at, sa.DateTime(timezone=True)),
                max_attempts,
            )
        )
    ).scalar_one()


def get_job(conn: Connection, course_id: UUID, job_id: UUID) -> RowMapping | None:
    stmt = sa.select(
        jobs.c.id,
        jobs.c.kind,
        jobs.c.target_id,
        jobs.c.state,
        jobs.c.attempts,
        jobs.c.max_attempts,
        jobs.c.run_after,
        jobs.c.deadline_at,
        jobs.c.error_code,
        jobs.c.created_at,
    ).where(jobs.c.course_id == course_id, jobs.c.id == job_id)
    return conn.execute(stmt).mappings().one_or_none()


def claim_job(
    conn: Connection, worker: str, *, kinds: list[str] | None = None
) -> RowMapping | None:
    claimed = sa.func.app.claim_job(
        worker, sa.cast(kinds, ARRAY(sa.Text))
    ).table_valued(*jobs.c.keys())
    return conn.execute(sa.select(claimed)).mappings().one_or_none()


def renew_job_lease(conn: Connection, job_id: UUID, lease_token: UUID) -> bool:
    return conn.scalar(sa.select(sa.func.app.renew_job_lease(job_id, lease_token))) is True


def complete_job(conn: Connection, job_id: UUID, lease_token: UUID) -> bool:
    return conn.scalar(sa.select(sa.func.app.finish_job(job_id, lease_token, None, False))) is True


def fail_job(
    conn: Connection, job_id: UUID, lease_token: UUID, *, error_code: str, retryable: bool = True
) -> bool:
    return (
        conn.scalar(sa.select(sa.func.app.finish_job(job_id, lease_token, error_code, retryable)))
        is True
    )


def recover_jobs(conn: Connection) -> int:
    """Recupera hasta cien trabajos vencidos que no estén bloqueados."""
    return conn.execute(sa.select(sa.func.app.recover_jobs())).scalar_one()


def install_jobs(conn: Connection) -> None:
    jobs.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.jobs ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.jobs FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS own_job_read ON app.jobs")
    conn.exec_driver_sql(
        """CREATE POLICY own_job_read ON app.jobs FOR SELECT TO tutoria_api
        USING (requested_by = app.course_actor()
            AND app.has_course_role(course_id, ARRAY['student', 'coordinator']))"""
    )
    conn.exec_driver_sql(
        """GRANT SELECT (id, course_id, kind, target_id, state, attempts, max_attempts,
            run_after, deadline_at, error_code, created_at) ON app.jobs TO tutoria_api"""
    )
    _install_enqueue(conn)
    _install_worker_operations(conn)
    _install_recovery(conn)


def _install_enqueue(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.enqueue_job(
            cid uuid, job_kind text, target uuid, request_key text, body jsonb,
            scheduled_at timestamptz, deadline timestamptz, attempt_limit integer
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            existing app.jobs;
            jid uuid;
            initial_at timestamptz;
        BEGIN
            PERFORM FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF job_kind IS NULL OR job_kind NOT IN ('ingest_document', 'generate_chat', 'generate_exam')
                OR request_key IS NULL OR char_length(request_key) NOT BETWEEN 1 AND 128
                OR request_key !~ '[^[:space:]]' OR jsonb_typeof(body) IS DISTINCT FROM 'object'
                OR attempt_limit IS NULL OR attempt_limit NOT BETWEEN 1 AND 3
                OR deadline IS NULL OR NOT isfinite(deadline)
                OR (scheduled_at IS NOT NULL AND NOT isfinite(scheduled_at)) THEN
                RAISE EXCEPTION 'INVALID_JOB' USING ERRCODE = '23514';
            END IF;

            SELECT * INTO existing FROM app.jobs
            WHERE course_id = cid AND requested_by = actor
              AND kind = job_kind AND idempotency_key = request_key;
            IF NOT FOUND THEN
                IF job_kind = 'ingest_document' THEN
                    PERFORM FROM app.documents AS d WHERE d.id = target AND d.course_id = cid
                      AND d.status = 'active'
                      AND (d.uploaded_by = actor OR app.has_course_role(cid, ARRAY['coordinator']));
                ELSE
                    PERFORM FROM app.generation_runs AS g WHERE g.id = target AND g.course_id = cid
                      AND g.user_id = actor AND g.status IN ('queued', 'running')
                      AND g.kind = CASE WHEN job_kind = 'generate_chat' THEN 'chat' ELSE 'exam' END;
                END IF;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'INVALID_JOB_TARGET' USING ERRCODE = '23514';
                END IF;

                initial_at := coalesce(scheduled_at, clock_timestamp());
                IF deadline <= greatest(initial_at, clock_timestamp()) THEN
                    RAISE EXCEPTION 'INVALID_JOB_DEADLINE' USING ERRCODE = '23514';
                END IF;
                INSERT INTO app.jobs (
                    id, course_id, requested_by, kind, target_id, payload,
                    idempotency_key, max_attempts, scheduled_at, run_after, deadline_at
                ) VALUES (
                    gen_random_uuid(), cid, actor, job_kind, target, body,
                    request_key, attempt_limit, scheduled_at, initial_at, deadline
                )
                ON CONFLICT (course_id, requested_by, kind, idempotency_key) DO NOTHING
                RETURNING id INTO jid;
                IF FOUND THEN
                    RETURN jid;
                END IF;
                SELECT * INTO STRICT existing FROM app.jobs
                WHERE course_id = cid AND requested_by = actor
                  AND kind = job_kind AND idempotency_key = request_key;
            END IF;

            IF ROW(existing.target_id, existing.payload, existing.max_attempts,
                    existing.scheduled_at, existing.deadline_at)
                IS DISTINCT FROM ROW(target, body, attempt_limit, scheduled_at, deadline) THEN
                RAISE EXCEPTION 'JOB_IDEMPOTENCY_CONFLICT' USING ERRCODE = '23505';
            END IF;
            RETURN existing.id;
        END
        $$"""
    )
    signature = "app.enqueue_job(uuid, text, uuid, text, jsonb, timestamptz, timestamptz, integer)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")


def _install_worker_operations(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.claim_job(worker text, supported_kinds text[])
        RETURNS SETOF app.jobs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            IF worker IS NULL OR char_length(worker) NOT BETWEEN 1 AND 128
                OR worker !~ '[^[:space:]]' THEN
                RAISE EXCEPTION 'INVALID_WORKER' USING ERRCODE = '23514';
            END IF;
            RETURN QUERY
            WITH candidate AS (
                SELECT j.id FROM app.jobs j
                WHERE j.state = 'queued' AND j.run_after <= clock_timestamp()
                    AND EXISTS (
                        SELECT FROM app.courses c
                        JOIN app.memberships m ON m.course_id = c.id
                        JOIN app.users u ON u.id = m.user_id
                        WHERE c.id = j.course_id AND c.status = 'active'
                          AND u.id = j.requested_by AND u.status = 'active' AND m.status = 'active'
                    )
                    AND CASE WHEN j.kind = 'ingest_document' THEN EXISTS (
                        SELECT FROM app.documents d WHERE d.id = j.target_id
                          AND d.course_id = j.course_id AND d.status = 'active'
                    ) ELSE EXISTS (
                        SELECT FROM app.generation_runs g WHERE g.id = j.target_id
                          AND g.course_id = j.course_id AND g.user_id = j.requested_by
                          AND g.status IN ('queued', 'running')
                    ) END
                    AND j.attempts < j.max_attempts AND j.deadline_at > clock_timestamp()
                    AND (supported_kinds IS NULL OR j.kind = ANY(supported_kinds))
                ORDER BY j.run_after, j.created_at, j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1
            )
            UPDATE app.jobs AS j SET state = 'running', attempts = j.attempts + 1,
                lease_token = gen_random_uuid(), locked_by = worker,
                lease_until = least(j.deadline_at, clock_timestamp() + interval '120 seconds'),
                error_code = NULL
            FROM candidate WHERE j.id = candidate.id AND j.deadline_at > clock_timestamp()
            RETURNING j.*;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.renew_job_lease(jid uuid, token uuid) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM FROM app.jobs WHERE id = jid AND state = 'running' AND lease_token = token FOR UPDATE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            UPDATE app.jobs SET
                lease_until = least(deadline_at, clock_timestamp() + interval '120 seconds')
            WHERE id = jid AND state = 'running' AND lease_token = token
              AND lease_until > clock_timestamp() AND deadline_at > clock_timestamp();
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.finish_job(
            jid uuid, token uuid, failure text, retryable boolean
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            IF failure IS NOT NULL AND failure !~ '^[A-Z0-9_]{1,80}$' THEN
                RAISE EXCEPTION 'INVALID_JOB_ERROR' USING ERRCODE = '23514';
            END IF;
            PERFORM FROM app.jobs WHERE id = jid AND state = 'running' AND lease_token = token FOR UPDATE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            UPDATE app.jobs SET state = CASE
                    WHEN failure IS NULL THEN 'succeeded'
                    WHEN retryable AND attempts < max_attempts
                      AND deadline_at > clock_timestamp() + app.job_retry_delay(attempts) THEN 'queued'
                    ELSE 'failed' END,
                run_after = CASE WHEN failure IS NOT NULL
                    THEN clock_timestamp() + app.job_retry_delay(attempts) ELSE run_after END,
                lease_token = NULL, locked_by = NULL, lease_until = NULL, error_code = failure
            WHERE id = jid AND state = 'running' AND lease_token = token
              AND lease_until > clock_timestamp() AND deadline_at > clock_timestamp();
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.claim_job(worker text) RETURNS SETOF app.jobs
        LANGUAGE sql SET search_path = pg_catalog, pg_temp
        AS $$ SELECT * FROM app.claim_job(worker, NULL::text[]) $$"""
    )
    signatures = (
        "app.claim_job(text), app.claim_job(text, text[]), "
        "app.renew_job_lease(uuid, uuid), app.finish_job(uuid, uuid, text, boolean)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_worker")


def _install_recovery(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.job_retry_delay(attempts integer) RETURNS interval
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $$ SELECT CASE WHEN attempts <= 1 THEN interval '10 seconds' ELSE interval '30 seconds' END $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.recover_jobs() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE recovered integer;
        BEGIN
            WITH expired AS (
                SELECT id FROM app.jobs
                WHERE (state = 'running' AND lease_until <= clock_timestamp())
                   OR (state IN ('queued', 'running') AND deadline_at <= clock_timestamp())
                ORDER BY deadline_at, id FOR UPDATE SKIP LOCKED LIMIT 100
            )
            UPDATE app.jobs AS j SET state = CASE
                    WHEN j.deadline_at <= clock_timestamp() + app.job_retry_delay(j.attempts)
                      OR j.attempts >= j.max_attempts THEN 'failed' ELSE 'queued' END,
                run_after = clock_timestamp() + app.job_retry_delay(j.attempts),
                lease_token = NULL, locked_by = NULL, lease_until = NULL,
                error_code = CASE
                    WHEN j.deadline_at <= clock_timestamp() + app.job_retry_delay(j.attempts)
                      THEN 'DEADLINE_EXCEEDED'
                    WHEN j.attempts >= j.max_attempts THEN 'RETRY_LIMIT_EXCEEDED'
                    ELSE 'LEASE_EXPIRED' END
            FROM expired WHERE j.id = expired.id;
            GET DIAGNOSTICS recovered = ROW_COUNT;
            RETURN recovered;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.fail_job_generation() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.generation_runs SET status = 'failed'
            WHERE id = NEW.target_id AND course_id = NEW.course_id AND user_id = NEW.requested_by
              AND status IN ('queued', 'running');
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS fail_job_generation ON app.jobs")
    conn.exec_driver_sql(
        """CREATE TRIGGER fail_job_generation AFTER UPDATE OF state ON app.jobs FOR EACH ROW
        WHEN (NEW.kind IN ('generate_chat', 'generate_exam') AND NEW.state = 'failed'
            AND OLD.state IS DISTINCT FROM NEW.state)
        EXECUTE FUNCTION app.fail_job_generation()"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.recover_jobs(), app.job_retry_delay(integer), "
        "app.fail_job_generation() FROM PUBLIC"
    )
    conn.exec_driver_sql("GRANT EXECUTE ON FUNCTION app.recover_jobs() TO tutoria_worker")
