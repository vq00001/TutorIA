"""Búsqueda exacta por coseno, agrupada por padre como en el ZIP de TutorIA."""

from uuid import UUID

from sqlalchemy import cast, func, select
from sqlalchemy.engine import Connection, RowMapping

from .processing import Vector, vector_text


def install_vector_search(conn: Connection) -> None:
    """Instala la búsqueda sin conceder acceso directo a las tablas de vectores."""
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.search_course_chunks(
            cid uuid, space_id uuid, query_vector public.vector,
            result_limit integer, threshold double precision
        ) RETURNS TABLE (
            document_id uuid, run_id uuid, embedding_space_id uuid, parent_id uuid,
            parent_text text, section_path text[], locator jsonb, child_id uuid,
            child_text text, start_offset integer, end_offset integer, score double precision
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE expected_dimension integer;
        BEGIN
            IF NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RETURN;
            END IF;
            IF result_limit IS NULL OR result_limit NOT BETWEEN 1 AND 12
                OR threshold IS NULL OR threshold NOT BETWEEN -1 AND 1 THEN
                RAISE EXCEPTION 'INVALID_SEARCH_PARAMETERS' USING ERRCODE = '22023';
            END IF;

            SELECT s.dimension INTO expected_dimension FROM app.embedding_spaces AS s
            WHERE s.id = space_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'EMBEDDING_SPACE_MISMATCH' USING ERRCODE = '22023';
            END IF;
            IF query_vector IS NULL OR public.vector_dims(query_vector) <> expected_dimension
                OR public.vector_norm(query_vector) = 0 THEN
                RAISE EXCEPTION 'INVALID_SEARCH_VECTOR' USING ERRCODE = '22023';
            END IF;

            RETURN QUERY
            WITH eligible AS MATERIALIZED (
                SELECT child.parent_id, child.id AS child_id, r.id AS run_id,
                    child.start_offset, child.end_offset,
                    1 - (e.embedding OPERATOR(public.<=>) query_vector) AS similarity
                FROM app.documents AS d
                JOIN app.processing_runs AS r
                  ON r.course_id = d.course_id AND r.document_id = d.id AND r.id = d.active_run_id
                JOIN app.child_chunks AS child
                  ON child.course_id = r.course_id AND child.run_id = r.id
                JOIN app.chunk_embeddings AS e
                  ON e.course_id = child.course_id AND e.run_id = child.run_id AND e.child_id = child.id
                WHERE d.course_id = cid AND d.status = 'active' AND r.state = 'ready'
                  AND r.embedding_space_id = space_id AND e.embedding_space_id = space_id
            ), best AS (
                SELECT DISTINCT ON (item.parent_id) item.*
                FROM eligible AS item
                ORDER BY item.parent_id, item.similarity DESC, item.child_id
            )
            SELECT p.document_id, best.run_id, space_id, p.id, p.text, p.section_path,
                   p.locator, best.child_id, child.text, best.start_offset, best.end_offset,
                   best.similarity
            FROM best
            JOIN app.parent_chunks AS p ON p.id = best.parent_id AND p.run_id = best.run_id
            JOIN app.child_chunks AS child ON child.id = best.child_id AND child.run_id = best.run_id
            WHERE best.similarity >= threshold
            ORDER BY best.similarity DESC, p.id
            LIMIT result_limit;
        END
        $$"""
    )
    signature = "app.search_course_chunks(uuid, uuid, public.vector, integer, double precision)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api, tutoria_worker")


def search_course_chunks(
    conn: Connection,
    course_id: UUID,
    embedding_space_id: UUID,
    vector: list[float],
    *,
    limit: int = 4,
    threshold: float = 0.5,
) -> list[RowMapping]:
    """Devuelve el mejor hijo de cada padre visible, ordenado por similitud."""
    if not 1 <= limit <= 12 or not -1 <= threshold <= 1:
        raise ValueError("INVALID_SEARCH_PARAMETERS")
    hits = func.app.search_course_chunks(
        course_id,
        embedding_space_id,
        cast(vector_text(vector), Vector()),
        limit,
        threshold,
    ).table_valued(
        "document_id",
        "run_id",
        "embedding_space_id",
        "parent_id",
        "parent_text",
        "section_path",
        "locator",
        "child_id",
        "child_text",
        "start_offset",
        "end_offset",
        "score",
    )
    statement = select(hits).order_by(hits.c.score.desc(), hits.c.parent_id)
    return list(conn.execute(statement).mappings())
