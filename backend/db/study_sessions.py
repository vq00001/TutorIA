"""Sesiones e intervalos de estudio, adaptados del ZIP de TutorIA."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping

from .courses import memberships
from .learning_plans import install_private_learning_tables

study_sessions = sa.Table(
    "study_sessions",
    memberships.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.Column("active_seconds", sa.BigInteger, server_default="0", nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("course_id", "user_id", "id"),
    sa.CheckConstraint(
        "active_seconds >= 0 AND last_heartbeat_at >= started_at "
        "AND (ended_at IS NULL OR ended_at >= last_heartbeat_at)",
        name="study_session_bounds",
    ),
)
sa.Index(
    "study_sessions_one_open_idx",
    study_sessions.c.course_id,
    study_sessions.c.user_id,
    unique=True,
    postgresql_where=study_sessions.c.ended_at.is_(None),
)
study_intervals = sa.Table(
    "study_intervals",
    memberships.metadata,
    sa.Column("session_id", sa.Uuid, primary_key=True),
    sa.Column("sequence_no", sa.BigInteger, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "session_id"],
        ["app.study_sessions.course_id", "app.study_sessions.user_id", "app.study_sessions.id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "sequence_no > 0 AND end_at > start_at AND end_at - start_at <= interval '30 seconds'",
        name="study_interval_bounds",
    ),
)


def start_study_session(conn: Connection, course_id: UUID) -> UUID:
    return conn.execute(sa.select(sa.func.app.start_study_session(course_id))).scalar_one()


def get_study_session(conn: Connection, course_id: UUID, session_id: UUID) -> RowMapping | None:
    stmt = sa.select(study_sessions).where(
        study_sessions.c.course_id == course_id, study_sessions.c.id == session_id
    )
    return conn.execute(stmt).mappings().one_or_none()


def list_study_sessions(conn: Connection, course_id: UUID, *, limit: int = 50) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    stmt = (
        sa.select(study_sessions)
        .where(study_sessions.c.course_id == course_id)
        .order_by(study_sessions.c.started_at.desc(), study_sessions.c.id)
        .limit(limit)
    )
    return list(conn.execute(stmt).mappings())


def list_study_intervals(conn: Connection, course_id: UUID, session_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(study_intervals)
        .where(study_intervals.c.course_id == course_id, study_intervals.c.session_id == session_id)
        .order_by(study_intervals.c.sequence_no)
    )
    return list(conn.execute(stmt).mappings())


def heartbeat_study_session(conn: Connection, course_id: UUID, session_id: UUID) -> int:
    return conn.execute(
        sa.select(sa.func.app.update_study_session(course_id, session_id, False))
    ).scalar_one()


def finish_study_session(conn: Connection, course_id: UUID, session_id: UUID) -> int:
    return conn.execute(
        sa.select(sa.func.app.update_study_session(course_id, session_id, True))
    ).scalar_one()


def install_study_sessions(conn: Connection) -> None:
    install_private_learning_tables(conn, study_sessions, study_intervals)
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.start_study_session(cid uuid) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.lock_learning_course(cid);
            opened app.study_sessions;
            current_at timestamptz;
            sid uuid := gen_random_uuid();
        BEGIN
            PERFORM FROM app.memberships
            WHERE course_id = cid AND user_id = actor AND status = 'active' FOR NO KEY UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            SELECT * INTO opened FROM app.study_sessions
            WHERE course_id = cid AND user_id = actor AND ended_at IS NULL FOR UPDATE;
            current_at := clock_timestamp();
            IF FOUND THEN
                IF current_at <= opened.last_heartbeat_at + interval '120 seconds' THEN
                    RETURN opened.id;
                END IF;
                UPDATE app.study_sessions SET ended_at = opened.last_heartbeat_at WHERE id = opened.id;
            END IF;
            INSERT INTO app.study_sessions (id, course_id, user_id, started_at, last_heartbeat_at)
            VALUES (sid, cid, actor, current_at, current_at);
            RETURN sid;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.update_study_session(cid uuid, sid uuid, finish boolean)
        RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.lock_learning_course(cid);
            opened app.study_sessions;
            current_at timestamptz;
            elapsed interval;
            credited integer := 0;
        BEGIN
            SELECT * INTO opened FROM app.study_sessions
            WHERE id = sid AND course_id = cid AND user_id = actor FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF opened.ended_at IS NOT NULL THEN
                RETURN opened.active_seconds;
            END IF;

            current_at := greatest(clock_timestamp(), opened.last_heartbeat_at);
            elapsed := current_at - opened.last_heartbeat_at;
            IF elapsed > interval '120 seconds' THEN
                UPDATE app.study_sessions SET ended_at = opened.last_heartbeat_at WHERE id = sid;
                RETURN opened.active_seconds;
            END IF;
            IF elapsed >= interval '25 seconds' THEN
                credited := least(floor(extract(epoch FROM elapsed)), 30)::integer;
                INSERT INTO app.study_intervals (
                    session_id, sequence_no, course_id, user_id, start_at, end_at
                )
                SELECT sid, coalesce(max(sequence_no), 0) + 1, cid, actor,
                    current_at - make_interval(secs => credited), current_at
                FROM app.study_intervals WHERE session_id = sid;
            END IF;
            IF credited > 0 OR finish THEN
                UPDATE app.study_sessions
                SET active_seconds = opened.active_seconds + credited,
                    last_heartbeat_at = CASE WHEN credited > 0 THEN current_at ELSE opened.last_heartbeat_at END,
                    ended_at = CASE WHEN finish THEN current_at ELSE NULL END
                WHERE id = sid;
            END IF;
            RETURN opened.active_seconds + credited;
        END
        $$"""
    )
    signatures = "app.start_study_session(uuid), app.update_study_session(uuid, uuid, boolean)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")
