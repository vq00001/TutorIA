"""Registro transaccional de eventos de auditoría."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from .users import users

audit_events = sa.Table(
    "audit_events",
    users.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.func.gen_random_uuid()),
    sa.Column("course_id", sa.Uuid),
    sa.Column("actor_id", sa.Uuid, sa.ForeignKey(users.c.id, ondelete="SET NULL")),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("action", sa.String(80), nullable=False),
    sa.Column("entity_type", sa.String(40), nullable=False),
    sa.Column("entity_id", sa.Uuid),
    sa.Column("request_id", sa.Uuid, nullable=False),
    sa.Column("result", sa.Text, nullable=False),
    sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint(
        "source = 'user' OR (source = 'worker' AND actor_id IS NULL)",
        name="audit_source",
    ),
    sa.CheckConstraint("result IN ('success','denied','error')", name="audit_result"),
    sa.CheckConstraint("action ~ '^[a-z][a-z0-9_.]*$'", name="audit_action_code"),
    sa.CheckConstraint("entity_type ~ '^[a-z][a-z0-9_]*$'", name="audit_entity_code"),
    sa.CheckConstraint(
        """metadata = '{}'::jsonb OR (
            metadata - 'reason_code' = '{}'::jsonb
            AND (metadata->>'reason_code' IN (
                'not_authorized', 'invalid_input', 'conflict', 'internal_error', 'dependency_failure'
            )) IS TRUE
        )""",
        name="audit_metadata",
    ),
)
sa.Index("audit_course_idx", audit_events.c.course_id, audit_events.c.created_at.desc())
sa.Index("audit_actor_idx", audit_events.c.actor_id, audit_events.c.created_at.desc())
sa.Index("audit_request_idx", audit_events.c.request_id)
sa.Index("audit_created_idx", audit_events.c.created_at)


def record_audit_event(
    conn: Connection,
    *,
    action: str,
    entity_type: str,
    request_id: UUID,
    result: str,
    course_id: UUID | None = None,
    entity_id: UUID | None = None,
    reason_code: str | None = None,
) -> UUID:
    """Registra un evento del usuario asociado a la transacción."""
    return conn.scalar(sa.select(sa.func.app.record_user_audit(
        course_id, action, entity_type, entity_id, request_id, result, reason_code
    )))


def record_worker_audit_event(
    conn: Connection,
    *,
    action: str,
    entity_type: str,
    request_id: UUID,
    result: str,
    course_id: UUID | None = None,
    entity_id: UUID | None = None,
    reason_code: str | None = None,
) -> UUID:
    """Registra un evento del worker sin atribuirlo a un usuario."""
    return conn.scalar(sa.select(sa.func.app.record_worker_audit(
        course_id, action, entity_type, entity_id, request_id, result, reason_code
    )))


def install_audit(conn: Connection) -> None:
    audit_events.create(conn, checkfirst=True)
    conn.exec_driver_sql("REVOKE ALL ON app.audit_events FROM PUBLIC, tutoria_api, tutoria_worker")
    conn.exec_driver_sql("ALTER TABLE app.audit_events ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.audit_events FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS audit_visible ON app.audit_events")
    conn.exec_driver_sql(
        """CREATE POLICY audit_visible ON app.audit_events FOR SELECT TO tutoria_api
        USING (actor_id = app.course_actor()
               OR app.has_course_role(course_id, ARRAY['coordinator']))"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.audit_events TO tutoria_api")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.record_user_audit(
            cid uuid, event_action text, kind text, eid uuid, rid uuid, outcome text, reason text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            event_id uuid;
        BEGIN
            IF NOT EXISTS (SELECT FROM app.users WHERE id = actor AND status = 'active')
                OR (cid IS NOT NULL AND NOT app.has_course_role(cid, ARRAY['student', 'coordinator']))
            THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            INSERT INTO app.audit_events
                (course_id, actor_id, source, action, entity_type, entity_id, request_id, result, metadata)
            VALUES (cid, actor, 'user', event_action, kind, eid, rid, outcome,
                    jsonb_strip_nulls(jsonb_build_object('reason_code', reason)))
            RETURNING id INTO event_id;
            RETURN event_id;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.record_worker_audit(
            cid uuid, event_action text, kind text, eid uuid, rid uuid, outcome text, reason text
        ) RETURNS uuid
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            INSERT INTO app.audit_events
                (course_id, source, action, entity_type, entity_id, request_id, result, metadata)
            VALUES (cid, 'worker', event_action, kind, eid, rid, outcome,
                    jsonb_strip_nulls(jsonb_build_object('reason_code', reason)))
            RETURNING id
        $$"""
    )
    user_signature = "app.record_user_audit(uuid, text, text, uuid, uuid, text, text)"
    worker_signature = "app.record_worker_audit(uuid, text, text, uuid, uuid, text, text)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {user_signature} FROM PUBLIC, tutoria_worker")
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {worker_signature} FROM PUBLIC, tutoria_api")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {user_signature} TO tutoria_api")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {worker_signature} TO tutoria_worker")
