"""Citas de respuestas, adaptadas de la procedencia de fuentes del ZIP."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from .conversations import messages
from .generations import generation_sources

citations = sa.Table(
    "citations",
    messages.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "message_id", sa.Uuid, sa.ForeignKey("app.messages.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("label", sa.String(255), nullable=False),
    sa.Column("request_id", sa.Uuid),
    sa.Column("source_rank", sa.Integer),
    sa.Column("status", sa.Text, nullable=False, server_default="available"),
    sa.ForeignKeyConstraint(
        ["request_id", "source_rank"],
        ["app.generation_sources.request_id", "app.generation_sources.rank"],
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint("message_id", "ordinal"),
    sa.CheckConstraint("ordinal > 0 AND length(btrim(label)) > 0", name="citations_ordinal_label"),
    sa.CheckConstraint(
        "(status = 'available' AND request_id IS NOT NULL AND source_rank IS NOT NULL) OR "
        "(status = 'source_removed' AND request_id IS NULL AND source_rank IS NULL)",
        name="citations_status_source",
    ),
)
sa.Index("citations_source_idx", citations.c.request_id, citations.c.source_rank)


@dataclass(frozen=True)
class Citation:
    source_rank: int
    label: str


def append_cited_assistant_message(
    conn: Connection,
    course_id: UUID,
    conversation_id: UUID,
    parts: Sequence[dict[str, Any]],
    *,
    generation_id: UUID,
    in_reply_to: UUID,
    request_id: UUID,
    references: Sequence[Citation],
) -> UUID:
    """Guarda la respuesta y sus citas contra la solicitud que produjo la respuesta."""
    statement = sa.select(
        sa.func.app.append_cited_assistant_message(
            course_id,
            conversation_id,
            sa.cast(list(parts), JSONB),
            generation_id,
            in_reply_to,
            request_id,
            sa.cast([asdict(reference) for reference in references], JSONB),
        )
    )
    return conn.execute(statement).scalar_one()


def list_message_citations(conn: Connection, message_id: UUID) -> list[RowMapping]:
    """Lee las citas en orden y recupera los offsets desde la fuente validada."""
    statement = (
        sa.select(
            citations,
            generation_sources.c.document_id,
            generation_sources.c.run_id,
            generation_sources.c.parent_id,
            generation_sources.c.child_id,
            generation_sources.c.embedding_space_id,
            generation_sources.c.start_offset,
            generation_sources.c.end_offset,
            generation_sources.c.score,
        )
        .select_from(
            citations.outerjoin(
                generation_sources,
                (citations.c.request_id == generation_sources.c.request_id)
                & (citations.c.source_rank == generation_sources.c.rank),
            )
        )
        .where(citations.c.message_id == message_id)
        .order_by(citations.c.ordinal)
    )
    return list(conn.execute(statement).mappings())


def install_citations(conn: Connection) -> None:
    """Instala las citas y retira sus enlaces si se borra la fuente."""
    citations.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.citations ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.citations FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS own_citations_read ON app.citations")
    conn.exec_driver_sql(
        """CREATE POLICY own_citations_read ON app.citations FOR SELECT TO tutoria_api
        USING (EXISTS (SELECT FROM app.messages AS m WHERE m.id = citations.message_id))"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.citations TO tutoria_api")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.append_cited_assistant_message(
            cid uuid, conversation uuid, content jsonb, gid uuid,
            reply_id uuid, rid uuid, references_payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            answer_id uuid;
            inserted bigint;
        BEGIN
            IF gid IS NULL OR reply_id IS NULL OR jsonb_typeof(references_payload) IS DISTINCT FROM 'array'
                OR jsonb_array_length(references_payload) = 0 THEN
                RAISE EXCEPTION 'INVALID_CITATIONS' USING ERRCODE = '23514';
            END IF;

            -- Esta operación toma los bloqueos de la clase, la conversación y la generación.
            answer_id := app.append_conversation_message(cid, conversation, content, gid, reply_id);
            IF NOT EXISTS (
                SELECT FROM app.generation_requests AS r WHERE r.id = rid AND r.generation_id = gid
            ) THEN
                RAISE EXCEPTION 'INVALID_CITATION_REQUEST' USING ERRCODE = '23514';
            END IF;

            INSERT INTO app.citations (id, message_id, ordinal, label, request_id, source_rank)
            SELECT gen_random_uuid(), answer_id, item.ordinal, item.value ->> 'label',
                   source.request_id, source.rank
            FROM jsonb_array_elements(references_payload) WITH ORDINALITY AS item(value, ordinal)
            JOIN app.generation_sources AS source
              ON source.request_id = rid AND source.rank = (item.value ->> 'source_rank')::integer;

            GET DIAGNOSTICS inserted = ROW_COUNT;
            IF inserted <> jsonb_array_length(references_payload) THEN
                RAISE EXCEPTION 'INVALID_CITATION_SOURCE' USING ERRCODE = '23514';
            END IF;
            RETURN answer_id;
        END
        $$"""
    )
    signature = "app.append_cited_assistant_message(uuid, uuid, jsonb, uuid, uuid, uuid, jsonb)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.withdraw_source_citations() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.citations SET request_id = NULL, source_rank = NULL,
                status = 'source_removed', label = 'Fuente retirada'
            WHERE request_id = OLD.request_id AND source_rank = OLD.rank;
            RETURN OLD;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "DROP TRIGGER IF EXISTS withdraw_source_citations ON app.generation_sources"
    )
    conn.exec_driver_sql(
        """CREATE TRIGGER withdraw_source_citations BEFORE DELETE ON app.generation_sources
        FOR EACH ROW EXECUTE FUNCTION app.withdraw_source_citations()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.withdraw_source_citations() FROM PUBLIC")
