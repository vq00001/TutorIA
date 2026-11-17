"""Citas de respuestas, adaptadas de la procedencia de fuentes del ZIP."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from .conversations import messages

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
    result = func.app.list_message_citations(message_id).table_valued(
        "ordinal",
        "request_id",
        "source_rank",
        "document_id",
        "parent_id",
        "child_id",
        "start_offset",
        "end_offset",
        "status",
        "source_document_id",
        "source_name",
        "section_path",
        "locator",
        "snippet",
        "score",
        "label",
    )
    statement = sa.select(result)
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
    conn.exec_driver_sql("DROP FUNCTION IF EXISTS app.list_message_citations(uuid)")
    conn.exec_driver_sql(
        """CREATE FUNCTION app.list_message_citations(target_message uuid)
        RETURNS TABLE (
            ordinal integer, request_id uuid, source_rank integer, document_id uuid,
            parent_id uuid, child_id uuid, start_offset integer, end_offset integer,
            status text, source_document_id uuid, source_name text, section_path text[],
            locator jsonb, snippet text, score double precision, label text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = off
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM app.messages
                WHERE id = target_message AND user_id = app.course_actor()
            ) THEN
                RETURN;
            END IF;
            RETURN QUERY
            SELECT citation.ordinal, citation.request_id, citation.source_rank,
                   source.document_id, source.parent_id, source.child_id,
                   source.start_offset, source.end_offset, citation.status,
                   source.document_id, version.original_name::text, parent.section_path,
                   parent.locator, child.text, source.score, citation.label::text
            FROM app.citations AS citation
            LEFT JOIN app.generation_sources AS source
              ON source.request_id = citation.request_id
             AND source.rank = citation.source_rank
            LEFT JOIN app.document_versions AS version
              ON version.document_id = source.document_id AND version.version_no = 1
            LEFT JOIN app.parent_chunks AS parent
              ON parent.id = source.parent_id AND parent.run_id = source.run_id
            LEFT JOIN app.child_chunks AS child
              ON child.id = source.child_id AND child.run_id = source.run_id
            WHERE citation.message_id = target_message
            ORDER BY citation.ordinal;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.list_message_citations(uuid) FROM PUBLIC"
    )
    conn.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION app.list_message_citations(uuid) TO tutoria_api"
    )

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
