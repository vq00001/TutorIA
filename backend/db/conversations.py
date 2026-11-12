"""Historial privado de conversaciones y mensajes, adaptado del ZIP de TutorIA."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from .generations import generation_runs

metadata = generation_runs.metadata

conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint("course_id", "user_id", "id"),
    sa.CheckConstraint("length(btrim(title)) > 0", name="conversations_title"),
    sa.CheckConstraint(
        "status IN ('active','closed','deletion_pending')",
        name="conversations_status",
    ),
)
sa.Index(
    "conversations_history_idx",
    conversations.c.course_id,
    conversations.c.user_id,
    conversations.c.updated_at,
    conversations.c.id,
)
_generation_owner_key = sa.Index(
    "generation_runs_course_user_id_key",
    generation_runs.c.course_id,
    generation_runs.c.user_id,
    generation_runs.c.id,
    unique=True,
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("sequence_no", sa.BigInteger, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="completed"),
    sa.Column("parts", JSONB, nullable=False),
    sa.Column("schema_version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("in_reply_to", sa.Uuid),
    sa.Column("generation_id", sa.Uuid),
    sa.Column("redacted_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "conversation_id"],
        ["app.conversations.course_id", "app.conversations.user_id", "app.conversations.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "generation_id"],
        ["app.generation_runs.course_id", "app.generation_runs.user_id", "app.generation_runs.id"],
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "conversation_id", "in_reply_to"],
        [
            "app.messages.course_id",
            "app.messages.user_id",
            "app.messages.conversation_id",
            "app.messages.id",
        ],
        deferrable=True,
        initially="DEFERRED",
    ),
    sa.UniqueConstraint("course_id", "user_id", "conversation_id", "id"),
    sa.UniqueConstraint("conversation_id", "sequence_no"),
    sa.UniqueConstraint("generation_id"),
    sa.CheckConstraint("sequence_no > 0", name="messages_sequence"),
    sa.CheckConstraint(
        "(role = 'user' AND generation_id IS NULL AND in_reply_to IS NULL) OR "
        "(role = 'assistant' AND generation_id IS NOT NULL AND in_reply_to IS NOT NULL)",
        name="messages_role_and_provenance",
    ),
    sa.CheckConstraint(
        "(status = 'completed' AND redacted_at IS NULL) OR "
        "(status = 'redacted' AND role = 'assistant' AND redacted_at IS NOT NULL AND parts = '[]'::jsonb)",
        name="messages_status",
    ),
    sa.CheckConstraint(
        "CASE WHEN jsonb_typeof(parts) = 'array' THEN "
        "jsonb_array_length(parts) BETWEEN CASE WHEN status = 'redacted' THEN 0 ELSE 1 END AND 20 "
        "AND octet_length(parts::text) <= 262144 ELSE false END",
        name="messages_parts",
    ),
)


def create_conversation(conn: Connection, course_id: UUID, title: str) -> UUID:
    return conn.execute(sa.select(sa.func.app.create_conversation(course_id, title))).scalar_one()


def _append_message(
    conn: Connection,
    course_id: UUID,
    conversation_id: UUID,
    parts: Sequence[dict[str, Any]],
    *,
    generation_id: UUID | None,
    in_reply_to: UUID | None,
) -> UUID:
    statement = sa.select(
        sa.func.app.append_conversation_message(
            course_id,
            conversation_id,
            sa.cast(list(parts), JSONB),
            sa.cast(generation_id, sa.Uuid()),
            sa.cast(in_reply_to, sa.Uuid()),
        )
    )
    return conn.execute(statement).scalar_one()


def append_user_message(
    conn: Connection,
    course_id: UUID,
    conversation_id: UUID,
    parts: Sequence[dict[str, Any]],
) -> UUID:
    return _append_message(
        conn,
        course_id,
        conversation_id,
        parts,
        generation_id=None,
        in_reply_to=None,
    )


def append_assistant_message(
    conn: Connection,
    course_id: UUID,
    conversation_id: UUID,
    parts: Sequence[dict[str, Any]],
    *,
    generation_id: UUID,
    in_reply_to: UUID,
) -> UUID:
    return _append_message(
        conn,
        course_id,
        conversation_id,
        parts,
        generation_id=generation_id,
        in_reply_to=in_reply_to,
    )


def list_conversations(
    conn: Connection,
    course_id: UUID,
    *,
    limit: int = 50,
    before: tuple[datetime, UUID] | None = None,
) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = sa.select(conversations).where(conversations.c.course_id == course_id)
    if before is not None:
        statement = statement.where(
            sa.tuple_(conversations.c.updated_at, conversations.c.id) < sa.tuple_(*before)
        )
    statement = statement.order_by(
        conversations.c.updated_at.desc(), conversations.c.id.desc()
    ).limit(limit)
    return list(conn.execute(statement).mappings())


def list_messages(
    conn: Connection,
    course_id: UUID,
    conversation_id: UUID,
    *,
    limit: int = 50,
    after_sequence: int = 0,
) -> list[RowMapping]:
    if not 1 <= limit <= 100 or after_sequence < 0:
        raise ValueError("Parámetros de paginación inválidos")
    statement = (
        sa.select(messages)
        .where(
            messages.c.course_id == course_id,
            messages.c.conversation_id == conversation_id,
            messages.c.sequence_no > after_sequence,
        )
        .order_by(messages.c.sequence_no)
        .limit(limit)
    )
    return list(conn.execute(statement).mappings())


def install_conversations(conn: Connection) -> None:
    conversations.create(conn, checkfirst=True)
    _generation_owner_key.create(conn, checkfirst=True)
    messages.create(conn, checkfirst=True)
    _install_conversation_access(conn)
    _install_conversation_writes(conn)
    _install_message_redaction(conn)


def _install_conversation_access(conn: Connection) -> None:
    policies = {
        "conversations": (
            "user_id = app.course_actor() AND status <> 'deletion_pending' "
            "AND app.has_course_role(course_id, ARRAY['student', 'coordinator'])"
        ),
        "messages": (
            "EXISTS (SELECT FROM app.conversations AS c WHERE c.id = messages.conversation_id "
            "AND c.course_id = messages.course_id AND c.user_id = messages.user_id)"
        ),
    }
    for table, predicate in policies.items():
        conn.exec_driver_sql(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS own_history ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE POLICY own_history ON app.{table} FOR SELECT TO tutoria_api USING ({predicate})"
        )
        conn.exec_driver_sql(f"GRANT SELECT ON app.{table} TO tutoria_api")


def _install_conversation_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.lock_history_scope(cid uuid, actor uuid)
        RETURNS boolean
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.lock_history_scope(uuid, uuid) FROM PUBLIC")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.create_conversation(cid uuid, conversation_title text)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            conversation uuid := gen_random_uuid();
            recorded_at timestamptz;
        BEGIN
            IF NOT app.lock_history_scope(cid, actor) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            recorded_at := clock_timestamp();
            INSERT INTO app.conversations (id, course_id, user_id, title, created_at, updated_at)
            VALUES (conversation, cid, actor, conversation_title, recorded_at, recorded_at);
            RETURN conversation;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.append_conversation_message(
            cid uuid, conversation uuid, content jsonb, gid uuid, reply_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            message_id uuid := gen_random_uuid();
            next_sequence bigint;
            message_role text;
            recorded_at timestamptz;
        BEGIN
            IF NOT app.lock_history_scope(cid, actor) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.conversations AS c
            WHERE c.id = conversation AND c.course_id = cid AND c.user_id = actor
              AND c.status = 'active' FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            IF gid IS NULL AND reply_id IS NULL THEN
                message_role := 'user';
            ELSE
                IF gid IS NULL OR reply_id IS NULL OR NOT EXISTS (
                    SELECT FROM app.messages AS m
                    WHERE m.id = reply_id AND m.conversation_id = conversation
                      AND m.course_id = cid AND m.user_id = actor AND m.role = 'user'
                ) THEN
                    RAISE EXCEPTION 'INVALID_REPLY_TARGET' USING ERRCODE = '23514';
                END IF;

                UPDATE app.generation_runs AS g SET status = 'completed'
                WHERE g.id = gid AND g.course_id = cid AND g.user_id = actor
                  AND g.kind = 'chat' AND g.status = 'running'
                  AND EXISTS (SELECT FROM app.generation_requests AS r WHERE r.generation_id = g.id);
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'INVALID_REPLY_GENERATION' USING ERRCODE = '23514';
                END IF;
                message_role := 'assistant';
            END IF;

            SELECT coalesce(max(m.sequence_no), 0) + 1 INTO next_sequence
            FROM app.messages AS m WHERE m.conversation_id = conversation;
            recorded_at := clock_timestamp();
            INSERT INTO app.messages (
                id, course_id, user_id, conversation_id, sequence_no, role,
                parts, in_reply_to, generation_id, created_at, updated_at
            ) VALUES (
                message_id, cid, actor, conversation, next_sequence, message_role,
                content, reply_id, gid, recorded_at, recorded_at
            );
            UPDATE app.conversations SET updated_at = recorded_at WHERE id = conversation;
            RETURN message_id;
        END
        $$"""
    )
    signatures = (
        "app.create_conversation(uuid, text), "
        "app.append_conversation_message(uuid, uuid, jsonb, uuid, uuid)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")


def _install_message_redaction(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.redact_generation_messages() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.messages SET status = 'redacted', parts = '[]'::jsonb,
                redacted_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE generation_id = NEW.id AND status <> 'redacted';
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS redact_generation_messages ON app.generation_runs")
    conn.exec_driver_sql(
        """CREATE TRIGGER redact_generation_messages AFTER UPDATE OF status ON app.generation_runs
        FOR EACH ROW WHEN (NEW.status = 'invalidated' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.redact_generation_messages()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.redact_generation_messages() FROM PUBLIC")
