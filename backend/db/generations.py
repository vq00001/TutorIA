"""Registro privado de solicitudes al modelo y su procedencia."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.engine import Connection, RowMapping

from .processing import chunk_embeddings

metadata = chunk_embeddings.metadata

generation_runs = sa.Table(
    "generation_runs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model_name", sa.Text, nullable=False),
    sa.Column("model_revision", sa.Text, nullable=False),
    sa.Column("prompt_version", sa.Text, nullable=False),
    sa.Column("corpus_revision", sa.BigInteger, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="running"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "kind IN ('chat', 'exam', 'concepts', 'roadmap_explanation')",
        name="generation_runs_kind",
    ),
    sa.CheckConstraint(
        "status IN ('queued','running','completed','failed','cancelled','invalidated')",
        name="generation_runs_status",
    ),
)
sa.Index("generation_runs_owner_idx", generation_runs.c.course_id, generation_runs.c.user_id)

generation_requests = sa.Table(
    "generation_requests",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "generation_id",
        sa.Uuid,
        sa.ForeignKey("app.generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("request_body", sa.LargeBinary, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("octet_length(request_body) > 0", name="generation_requests_body"),
)
sa.Index("generation_requests_generation_idx", generation_requests.c.generation_id)

generation_sources = sa.Table(
    "generation_sources",
    metadata,
    sa.Column(
        "request_id",
        sa.Uuid,
        sa.ForeignKey("app.generation_requests.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("rank", sa.Integer, primary_key=True),
    sa.Column("document_id", sa.Uuid, nullable=False),
    sa.Column("run_id", sa.Uuid, nullable=False),
    sa.Column("parent_id", sa.Uuid, nullable=False),
    sa.Column("child_id", sa.Uuid, nullable=False),
    sa.Column("embedding_space_id", sa.Uuid, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("score", sa.Double, nullable=False),
    sa.ForeignKeyConstraint(
        ["child_id", "embedding_space_id"],
        ["app.chunk_embeddings.child_id", "app.chunk_embeddings.embedding_space_id"],
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint("request_id", "parent_id"),
    sa.CheckConstraint(
        "rank > 0 AND 0 <= start_offset AND start_offset < end_offset",
        name="generation_sources_bounds",
    ),
    sa.CheckConstraint("score BETWEEN -1 AND 1", name="generation_sources_score"),
)
sa.Index(
    "generation_sources_embedding_idx",
    generation_sources.c.child_id,
    generation_sources.c.embedding_space_id,
)

_source_fields = (
    "document_id",
    "run_id",
    "parent_id",
    "child_id",
    "embedding_space_id",
    "start_offset",
    "end_offset",
    "score",
)


def start_generation(
    conn: Connection,
    course_id: UUID,
    *,
    kind: str,
    provider: str,
    model_name: str,
    model_revision: str,
    prompt_version: str,
) -> UUID:
    """Registra la generación y el estado del corpus al comenzar."""
    statement = sa.select(
        sa.func.app.start_generation(
            course_id,
            kind,
            provider,
            model_name,
            model_revision,
            prompt_version,
        )
    )
    return conn.execute(statement).scalar_one()


def stop_generation(
    conn: Connection,
    generation_id: UUID,
    *,
    status: Literal["failed", "cancelled"],
) -> bool:
    """Cierra una generación activa propia y libera su cupo."""
    return conn.scalar(sa.select(sa.func.app.stop_generation(generation_id, status))) is True


def record_generation_request(
    conn: Connection,
    generation_id: UUID,
    request_body: bytes,
    sources: Sequence[Mapping[str, Any]],
) -> UUID:
    """Registra antes del envío los mismos bytes que usará el cliente del modelo."""
    if not isinstance(request_body, bytes):
        raise TypeError("request_body debe contener los bytes exactos de la solicitud")
    try:
        source_records = [
            {
                key: str(source[key]) if isinstance(source[key], UUID) else source[key]
                for key in _source_fields
            }
            for source in sources
        ]
    except KeyError as exc:
        raise ValueError("INVALID_GENERATION_SOURCE") from exc
    statement = sa.select(
        sa.func.app.record_generation_request(
            generation_id,
            request_body,
            sa.cast(source_records, JSONB),
        )
    )
    return conn.execute(statement).scalar_one()


def get_generation_request(conn: Connection, request_id: UUID) -> RowMapping | None:
    """Recupera el cuerpo y las fuentes ordenadas para la cuenta que los generó."""
    sources = (
        sa.select(
            sa.func.jsonb_agg(
                aggregate_order_by(
                    sa.func.to_jsonb(generation_sources.table_valued()),
                    generation_sources.c.rank,
                )
            )
        )
        .where(generation_sources.c.request_id == generation_requests.c.id)
        .scalar_subquery()
    )
    statement = sa.select(
        generation_requests,
        sa.func.coalesce(sources, sa.cast([], JSONB)).label("sources"),
    ).where(generation_requests.c.id == request_id)
    return conn.execute(statement).mappings().one_or_none()


def install_generations(conn: Connection) -> None:
    """Instala el registro privado y su limpieza al perder una fuente."""
    for table in (generation_runs, generation_requests, generation_sources):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")
    _install_generation_access(conn)
    _install_generation_writes(conn)
    _install_generation_cleanup(conn)


def _install_generation_access(conn: Connection) -> None:
    policies = {
        "generation_runs": (
            "user_id = app.course_actor() "
            "AND app.has_course_role(course_id, ARRAY['student', 'coordinator'])"
        ),
        "generation_requests": (
            "EXISTS (SELECT FROM app.generation_runs AS g "
            "WHERE g.id = generation_requests.generation_id)"
        ),
        "generation_sources": (
            "EXISTS (SELECT FROM app.generation_requests AS r "
            "WHERE r.id = generation_sources.request_id)"
        ),
    }
    for table, predicate in policies.items():
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS own_generation_read ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE POLICY own_generation_read ON app.{table} FOR SELECT TO tutoria_api "
            f"USING ({predicate})"
        )
        conn.exec_driver_sql(f"GRANT SELECT ON app.{table} TO tutoria_api")


def _install_generation_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.start_generation(
            cid uuid, generation_kind text, model_provider text,
            requested_model text, requested_revision text, requested_prompt text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            revision bigint;
            generation uuid := gen_random_uuid();
        BEGIN
            SELECT corpus_revision INTO revision
            FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            INSERT INTO app.generation_runs (
                id, course_id, user_id, kind, provider, model_name, model_revision,
                prompt_version, corpus_revision
            ) VALUES (
                generation, cid, actor, generation_kind, model_provider, requested_model,
                requested_revision, requested_prompt, revision
            );
            RETURN generation;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.record_generation_request(
            gid uuid, body bytea, source_payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            generation app.generation_runs;
            request_id uuid := gen_random_uuid();
            payload jsonb;
            inserted bigint;
        BEGIN
            SELECT * INTO generation FROM app.generation_runs
            WHERE id = gid AND user_id = app.course_actor();
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            -- El borrado y la publicación también bloquean primero la clase.
            PERFORM 1 FROM app.courses WHERE id = generation.course_id AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(generation.course_id, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.users WHERE id = generation.user_id AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.generation_runs WHERE id = gid AND status = 'running' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'GENERATION_NOT_ACTIVE' USING ERRCODE = '22023';
            END IF;

            payload := convert_from(body, 'UTF8')::jsonb;
            IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
                OR jsonb_typeof(payload -> 'messages') IS DISTINCT FROM 'array'
                OR jsonb_typeof(source_payload) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'INVALID_GENERATION_REQUEST' USING ERRCODE = '22023';
            END IF;

            INSERT INTO app.generation_requests (id, generation_id, request_body)
            VALUES (request_id, gid, body);
            INSERT INTO app.generation_sources (
                request_id, rank, document_id, run_id, parent_id, child_id, embedding_space_id,
                start_offset, end_offset, score
            )
            SELECT request_id, item.rank, d.id, r.id, child.parent_id, child.id, e.embedding_space_id,
                   child.start_offset, child.end_offset, (item.value ->> 'score')::double precision
            FROM jsonb_array_elements(source_payload) WITH ORDINALITY AS item(value, rank)
            JOIN app.child_chunks AS child ON child.id = (item.value ->> 'child_id')::uuid
            JOIN app.processing_runs AS r ON r.id = child.run_id AND r.course_id = child.course_id
            JOIN app.documents AS d
              ON d.id = r.document_id AND d.course_id = r.course_id AND d.active_run_id = r.id
            JOIN app.chunk_embeddings AS e
              ON e.child_id = child.id AND e.run_id = r.id AND e.course_id = r.course_id
            WHERE d.course_id = generation.course_id AND d.status = 'active' AND r.state = 'ready'
              AND d.id = (item.value ->> 'document_id')::uuid
              AND r.id = (item.value ->> 'run_id')::uuid
              AND child.parent_id = (item.value ->> 'parent_id')::uuid
              AND e.embedding_space_id = (item.value ->> 'embedding_space_id')::uuid
              AND child.start_offset = (item.value ->> 'start_offset')::int
              AND child.end_offset = (item.value ->> 'end_offset')::int;

            GET DIAGNOSTICS inserted = ROW_COUNT;
            IF inserted <> jsonb_array_length(source_payload) THEN
                RAISE EXCEPTION 'INVALID_GENERATION_SOURCE' USING ERRCODE = '23514';
            END IF;
            RETURN request_id;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.stop_generation(gid uuid, final_status text)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            cid uuid;
        BEGIN
            IF final_status IS NULL OR final_status NOT IN ('failed', 'cancelled') THEN
                RAISE EXCEPTION 'INVALID_GENERATION_STATUS' USING ERRCODE = '22023';
            END IF;
            SELECT course_id INTO cid FROM app.generation_runs WHERE id = gid AND user_id = actor;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            PERFORM FROM app.courses WHERE id = cid AND status IN ('active', 'archived') FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RETURN false;
            END IF;
            UPDATE app.generation_runs SET status = final_status
            WHERE id = gid AND user_id = actor AND status IN ('queued', 'running');
            RETURN FOUND;
        END
        $$"""
    )
    signatures = (
        "app.start_generation(uuid, text, text, text, text, text), "
        "app.record_generation_request(uuid, bytea, jsonb), app.stop_generation(uuid, text)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")


def _install_generation_cleanup(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.invalidate_embedding_generations() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.generation_runs AS g SET status = 'invalidated'
            WHERE g.status <> 'invalidated' AND EXISTS (
                SELECT FROM app.generation_requests AS r
                JOIN app.generation_sources AS s ON s.request_id = r.id
                WHERE r.generation_id = g.id
                  AND s.child_id = OLD.child_id AND s.embedding_space_id = OLD.embedding_space_id
            );
            RETURN OLD;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.clear_invalidated_generation_requests() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            DELETE FROM app.generation_requests WHERE generation_id = NEW.id;
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "DROP TRIGGER IF EXISTS invalidate_embedding_generations ON app.chunk_embeddings"
    )
    conn.exec_driver_sql(
        """CREATE TRIGGER invalidate_embedding_generations BEFORE DELETE ON app.chunk_embeddings
        FOR EACH ROW EXECUTE FUNCTION app.invalidate_embedding_generations()"""
    )
    conn.exec_driver_sql(
        "DROP TRIGGER IF EXISTS clear_invalidated_generation_requests ON app.generation_runs"
    )
    conn.exec_driver_sql(
        """CREATE TRIGGER clear_invalidated_generation_requests AFTER UPDATE OF status ON app.generation_runs
        FOR EACH ROW WHEN (NEW.status = 'invalidated' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.clear_invalidated_generation_requests()"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.invalidate_embedding_generations(), "
        "app.clear_invalidated_generation_requests() FROM PUBLIC"
    )
