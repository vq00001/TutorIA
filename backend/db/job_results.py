"""Entradas de PostgreSQL para publicar resultados con una reserva vigente."""

from sqlalchemy.engine import Connection

from .connection import RESTORE_LOCK

# Cada entrada deriva clase, propietario y objetivo de la reserva, no del llamador.
_OPERATIONS = (
    (
        "record_job_request", "body bytea, sources jsonb", "bytea, jsonb",
        "generate_chat', 'generate_exam",
        "app.record_generation_request(job.target_id, body, sources)", False,
    ),
    (
        "publish_chat_job",
        "conversation uuid, content jsonb, reply_id uuid, rid uuid, citations jsonb",
        "uuid, jsonb, uuid, uuid, jsonb", "generate_chat",
        """CASE WHEN citations = '[]'::jsonb
             THEN app.append_conversation_message(job.course_id, conversation, content,
                                                  job.target_id, reply_id)
             ELSE app.append_cited_assistant_message(job.course_id, conversation, content,
                                                      job.target_id, reply_id, rid, citations)
             END""", True,
    ),
    (
        "publish_exam_job", "title text, exam_kind text, duration integer, payload jsonb",
        "text, text, integer, jsonb", "generate_exam",
        "app.publish_exam(job.course_id, job.target_id, title, exam_kind, duration, payload)", True,
    ),
    (
        "start_ingestion_job",
        "space uuid, digest bytea, parser text, chunker text, parameters jsonb",
        "uuid, bytea, text, text, jsonb", "ingest_document",
        """app.start_document_processing(job.course_id, job.target_id, space, digest,
                                         parser, chunker, parameters)""", False,
    ),
    (
        "publish_ingestion_job", "rid uuid, source_text text, parents jsonb, children jsonb",
        "uuid, text, jsonb, jsonb", "ingest_document",
        "CASE WHEN app.publish_document_processing_internal(rid, source_text, parents, children) THEN rid END",
        True,
    ),
)


def install_job_results(conn: Connection) -> None:
    conn.exec_driver_sql(
        f"""CREATE OR REPLACE FUNCTION app.lock_job_result(jid uuid, token uuid, kinds text[])
        RETURNS app.jobs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE job app.jobs;
        BEGIN
            PERFORM pg_advisory_xact_lock_shared({RESTORE_LOCK});
            IF NOT app.restore_is_ready() THEN
                RAISE EXCEPTION 'RESTORE_NOT_READY' USING ERRCODE = '42501';
            END IF;
            PERFORM FROM app.courses c JOIN app.jobs j ON j.course_id = c.id
            WHERE j.id = jid AND c.status = 'active' FOR UPDATE OF c;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'JOB_LEASE_LOST' USING ERRCODE = '42501';
            END IF;
            SELECT * INTO job FROM app.jobs
            WHERE id = jid AND state = 'running' AND lease_token = token
              AND kind = ANY(kinds) AND lease_until > clock_timestamp()
              AND deadline_at > clock_timestamp()
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'JOB_LEASE_LOST' USING ERRCODE = '42501';
            END IF;
            IF job.kind <> 'ingest_document' AND NOT EXISTS (
                SELECT FROM app.generation_runs
                WHERE id = job.target_id AND course_id = job.course_id
                  AND user_id = job.requested_by AND status = 'running'
            ) THEN
                RAISE EXCEPTION 'INVALID_JOB_TARGET' USING ERRCODE = '42501';
            END IF;
            RETURN job;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.lock_job_result(uuid, uuid, text[]) FROM PUBLIC, tutoria_api, tutoria_worker")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS processing_ingestion_job_idx "
        "ON app.processing_runs (ingestion_job_id) WHERE state = 'running'"
    )
    for name, args, types, kinds, expression, complete in _OPERATIONS:
        before, after = _operation_checks(name)
        finish = """
            IF result IS NULL OR NOT app.finish_job(jid, token, NULL, false) THEN
                RAISE EXCEPTION 'JOB_RESULT_REJECTED' USING ERRCODE = '42501';
            END IF;
        """ if complete else ""
        conn.exec_driver_sql(f"""
            CREATE OR REPLACE FUNCTION app.{name}(jid uuid, token uuid, {args}) RETURNS uuid
            LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
            AS $$
            DECLARE
                job app.jobs := app.lock_job_result(jid, token, ARRAY['{kinds}']);
                actor text := current_setting('app.user_id', true);
                result uuid;
            BEGIN
                PERFORM set_config('app.user_id', job.requested_by::text, true);
                {before}
                result := {expression};
                {after}
                {finish}
                PERFORM set_config('app.user_id', coalesce(actor, ''), true);
                RETURN result;
            END
            $$
        """)
        signature = f"app.{name}(uuid, uuid, {types})"
        conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, tutoria_api")
        conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_worker")
    _install_job_cleanup(conn)


def _operation_checks(name: str) -> tuple[str, str]:
    if name == "start_ingestion_job":
        return (
            """
            SELECT id INTO result FROM app.processing_runs
            WHERE ingestion_job_id = jid AND state = 'running' AND config_hash = digest;
            IF FOUND THEN
                PERFORM set_config('app.user_id', coalesce(actor, ''), true);
                RETURN result;
            END IF;
            """,
            """
            UPDATE app.processing_runs SET ingestion_job_id = jid WHERE id = result;
            IF result IS NULL AND EXISTS (
                SELECT FROM app.documents d JOIN app.processing_runs r ON r.id = d.active_run_id
                WHERE d.id = job.target_id AND d.status = 'active' AND r.config_hash = digest
            ) THEN
                IF NOT app.finish_job(jid, token, NULL, false) THEN
                    RAISE EXCEPTION 'JOB_LEASE_LOST' USING ERRCODE = '42501';
                END IF;
            ELSIF result IS NULL THEN
                RAISE EXCEPTION 'INGESTION_UNAVAILABLE' USING ERRCODE = '55000';
            END IF;
            """,
        )
    if name == "publish_ingestion_job":
        return ("""
            IF NOT EXISTS (
                SELECT FROM app.processing_runs WHERE id = rid AND ingestion_job_id = jid
                  AND document_id = job.target_id AND state = 'running'
            ) THEN
                RAISE EXCEPTION 'INVALID_JOB_TARGET' USING ERRCODE = '42501';
            END IF;
        """, "")
    if name == "publish_chat_job":
        return ("""
            IF jsonb_typeof(citations) IS DISTINCT FROM 'array' OR NOT EXISTS (
                SELECT FROM app.generation_requests WHERE id = rid AND generation_id = job.target_id
            ) OR (citations = '[]'::jsonb AND EXISTS (
                SELECT FROM app.generation_sources WHERE request_id = rid
            )) THEN
                RAISE EXCEPTION 'INVALID_JOB_REQUEST' USING ERRCODE = '23514';
            END IF;
        """, "")
    if name == "publish_exam_job":
        return ("""
            IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
                OR NOT EXISTS (
                    SELECT FROM app.generation_requests
                    WHERE id = (payload->>'request_id')::uuid AND generation_id = job.target_id
                ) THEN
                RAISE EXCEPTION 'INVALID_JOB_REQUEST' USING ERRCODE = '23514';
            END IF;
        """, "")
    return "", ""


def _install_job_cleanup(conn: Connection) -> None:
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION app.cleanup_job_target() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'generation_runs' THEN
                IF NEW.status IN ('cancelled', 'invalidated') THEN
                    DELETE FROM app.jobs WHERE target_id = NEW.id AND course_id = NEW.course_id
                      AND kind IN ('generate_chat', 'generate_exam')
                      AND state IN ('queued', 'running');
                END IF;
            ELSIF TG_TABLE_NAME = 'documents' THEN
                DELETE FROM app.jobs WHERE target_id = OLD.id AND course_id = OLD.course_id
                  AND kind = 'ingest_document' AND state IN ('queued', 'running');
            ELSIF NEW.kind = 'ingest_document' AND NEW.state = 'failed' THEN
                WITH failed AS (
                    UPDATE app.processing_runs
                    SET state = 'failed', finished_at = clock_timestamp(), error_code = NEW.error_code
                    WHERE ingestion_job_id = NEW.id AND state = 'running'
                    RETURNING version_id
                )
                UPDATE app.document_versions SET status = 'failed', error_code = NEW.error_code
                WHERE id IN (SELECT version_id FROM failed) AND status <> 'ready';
                UPDATE app.document_versions SET status = 'failed', error_code = NEW.error_code
                WHERE course_id = NEW.course_id AND document_id = NEW.target_id
                  AND version_no = 1 AND status = 'uploaded';
            END IF;
            RETURN NULL;
        END
        $$
    """)
    for table, event in (
        ("generation_runs", "UPDATE OF status"),
        ("documents", "DELETE"),
        ("jobs", "UPDATE OF state"),
    ):
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS cleanup_job_target ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE TRIGGER cleanup_job_target AFTER {event} ON app.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app.cleanup_job_target()"
        )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.cleanup_job_target() FROM PUBLIC")
