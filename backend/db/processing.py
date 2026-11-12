"""Ejecuciones, fragmentos y embeddings de documentos en PostgreSQL."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import isfinite, sqrt
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.types import UserDefinedType

from .documents import documents


class Vector(UserDefinedType):
    """Tipo vector sin dimensión fija. La dimensión depende del espacio."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "public.vector"


embedding_spaces = sa.Table(
    "embedding_spaces",
    documents.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model_name", sa.Text, nullable=False),
    sa.Column("model_revision", sa.Text, nullable=False),
    sa.Column("dimension", sa.Integer, nullable=False),
    sa.Column("distance", sa.Text, nullable=False, server_default="cosine"),
    sa.Column("normalization", sa.Text, nullable=False, server_default="l2"),
    sa.Column("query_prefix", sa.Text, nullable=False, server_default=""),
    sa.Column("document_prefix", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("dimension BETWEEN 1 AND 2000", name="embedding_spaces_dimension"),
    sa.CheckConstraint(
        "distance = 'cosine' AND normalization = 'l2'", name="embedding_spaces_metric"
    ),
    sa.UniqueConstraint(
        "provider",
        "model_name",
        "model_revision",
        "dimension",
        "query_prefix",
        "document_prefix",
        name="embedding_spaces_identity",
    ),
)

processing_runs = sa.Table(
    "processing_runs",
    documents.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("document_id", sa.Uuid, nullable=False),
    sa.Column("version_id", sa.Uuid, nullable=False),
    sa.Column("config_hash", sa.LargeBinary(32), nullable=False),
    sa.Column("parser_version", sa.Text, nullable=False),
    sa.Column("chunker_version", sa.Text, nullable=False),
    sa.Column(
        "embedding_space_id", sa.Uuid, sa.ForeignKey("app.embedding_spaces.id"), nullable=False
    ),
    sa.Column("parameters", JSONB, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("extracted_text", sa.Text),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.String(80)),
    sa.ForeignKeyConstraint(
        ["course_id", "document_id", "version_id"],
        [
            "app.document_versions.course_id",
            "app.document_versions.document_id",
            "app.document_versions.id",
        ],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("course_id", "document_id", "id"),
    sa.UniqueConstraint("course_id", "id", "embedding_space_id"),
    sa.CheckConstraint("octet_length(config_hash) = 32", name="processing_runs_config_hash"),
    sa.CheckConstraint("state IN ('running', 'ready', 'failed')", name="processing_runs_state"),
    sa.CheckConstraint("jsonb_typeof(parameters) = 'object'", name="processing_runs_parameters"),
    sa.CheckConstraint(
        "(state = 'running') = (finished_at IS NULL) AND (state = 'ready') = (extracted_text IS NOT NULL)",
        name="processing_runs_completion",
    ),
)
sa.Index(
    "processing_runs_one_running",
    processing_runs.c.version_id,
    unique=True,
    postgresql_where=processing_runs.c.state == "running",
)
sa.Index(
    "processing_runs_version_config", processing_runs.c.version_id, processing_runs.c.config_hash
)

parent_chunks = sa.Table(
    "parent_chunks",
    documents.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("document_id", sa.Uuid, nullable=False),
    sa.Column("run_id", sa.Uuid, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("section_path", sa.ARRAY(sa.Text), nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("locator", JSONB, nullable=False),
    sa.Column("token_estimate", sa.Integer, nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "document_id", "run_id"],
        [
            "app.processing_runs.course_id",
            "app.processing_runs.document_id",
            "app.processing_runs.id",
        ],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("run_id", "ordinal"),
    sa.UniqueConstraint("course_id", "document_id", "run_id", "id"),
    sa.CheckConstraint("ordinal >= 0 AND token_estimate > 0", name="parent_chunks_bounds"),
    sa.CheckConstraint("length(btrim(text)) > 0", name="parent_chunks_text"),
    sa.CheckConstraint("jsonb_typeof(locator) = 'object'", name="parent_chunks_locator"),
)

child_chunks = sa.Table(
    "child_chunks",
    documents.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("document_id", sa.Uuid, nullable=False),
    sa.Column("run_id", sa.Uuid, nullable=False),
    sa.Column("parent_id", sa.Uuid, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("embedding_text", sa.Text, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("token_estimate", sa.Integer, nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "document_id", "run_id", "parent_id"],
        [
            "app.parent_chunks.course_id",
            "app.parent_chunks.document_id",
            "app.parent_chunks.run_id",
            "app.parent_chunks.id",
        ],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("run_id", "ordinal"),
    sa.UniqueConstraint("course_id", "run_id", "id"),
    sa.CheckConstraint("ordinal >= 0 AND token_estimate > 0", name="child_chunks_bounds"),
    sa.CheckConstraint(
        "0 <= start_offset AND start_offset < end_offset", name="child_chunks_offsets"
    ),
    sa.CheckConstraint("length(btrim(embedding_text)) > 0", name="child_chunks_embedding_text"),
)
sa.Index("child_chunks_parent_idx", child_chunks.c.parent_id)

chunk_embeddings = sa.Table(
    "chunk_embeddings",
    documents.metadata,
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("run_id", sa.Uuid, nullable=False),
    sa.Column("child_id", sa.Uuid, primary_key=True),
    sa.Column("embedding_space_id", sa.Uuid, primary_key=True),
    sa.Column("embedding", Vector(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "run_id", "child_id"],
        ["app.child_chunks.course_id", "app.child_chunks.run_id", "app.child_chunks.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "run_id", "embedding_space_id"],
        [
            "app.processing_runs.course_id",
            "app.processing_runs.id",
            "app.processing_runs.embedding_space_id",
        ],
        ondelete="CASCADE",
    ),
)


@dataclass(frozen=True)
class ParentChunk:
    text: str
    section_path: list[str]
    locator: dict
    token_estimate: int


@dataclass(frozen=True)
class ChildChunk:
    parent_index: int
    text: str
    embedding_text: str
    start_offset: int
    end_offset: int
    token_estimate: int
    embedding: list[float]


def _vector_text(vector: list[float]) -> str:
    # Normalización L2 del ZIP, escalada para evitar desbordamiento.
    if not 1 <= len(vector) <= 2000 or any(
        isinstance(x, bool) or not isinstance(x, (int, float)) for x in vector
    ):
        raise ValueError("INVALID_VECTOR")
    try:
        values = [float(value) for value in vector]
    except OverflowError as exc:
        raise ValueError("INVALID_VECTOR") from exc
    if not all(isfinite(value) for value in values):
        raise ValueError("INVALID_VECTOR")
    scale = max(abs(value) for value in values)
    if scale == 0:
        raise ValueError("INVALID_VECTOR")
    scaled = [value / scale for value in values]
    norm = sqrt(sum(value * value for value in scaled))
    return "[" + ",".join(str(value / norm) for value in scaled) + "]"


def register_embedding_space(
    conn: Connection,
    *,
    provider: str,
    model_name: str,
    model_revision: str,
    dimension: int,
    query_prefix: str = "",
    document_prefix: str = "",
) -> UUID:
    """Registra o recupera la identidad del modelo desde una conexión administrativa."""
    identity = {
        "provider": provider,
        "model_name": model_name,
        "model_revision": model_revision,
        "dimension": dimension,
        "query_prefix": query_prefix,
        "document_prefix": document_prefix,
    }
    statement = (
        insert(embedding_spaces)
        .values(id=uuid4(), **identity)
        .on_conflict_do_nothing(constraint="embedding_spaces_identity")
        .returning(embedding_spaces.c.id)
    )
    identifier = conn.scalar(statement)
    if identifier is not None:
        return identifier
    return conn.execute(sa.select(embedding_spaces.c.id).filter_by(**identity)).scalar_one()


def start_processing(
    conn: Connection,
    course_id: UUID,
    document_id: UUID,
    *,
    embedding_space_id: UUID,
    parser_version: str,
    chunker_version: str,
    parameters: dict,
) -> UUID | None:
    """Inicia una ejecución. Devuelve None si el trabajo ya está en curso o listo."""
    config = json.dumps(
        [str(embedding_space_id), parser_version, chunker_version, parameters],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return conn.scalar(
        sa.select(
            sa.func.app.start_document_processing(
                course_id,
                document_id,
                embedding_space_id,
                sha256(config).digest(),
                parser_version,
                chunker_version,
                sa.cast(parameters, JSONB),
            )
        )
    )


def get_processing_source(conn: Connection, run_id: UUID) -> RowMapping | None:
    """Lee el original y la identidad del modelo para una ejecución en curso."""
    source = sa.func.app.document_processing_source(run_id).table_valued(
        "original_name",
        "mime_type",
        "bytes",
        "provider",
        "model_name",
        "model_revision",
        "dimension",
        "document_prefix",
    )
    return conn.execute(sa.select(source)).mappings().one_or_none()


def publish_processing(
    conn: Connection,
    run_id: UUID,
    *,
    extracted_text: str,
    parents: list[ParentChunk],
    children: list[ChildChunk],
) -> bool:
    """Guarda y publica todos los fragmentos de una ejecución en una transacción."""
    child_payload = []
    for child in children:
        values = asdict(child)
        values["embedding"] = _vector_text(child.embedding)
        child_payload.append(values)
    return (
        conn.scalar(
            sa.select(
                sa.func.app.publish_document_processing(
                    run_id,
                    extracted_text,
                    sa.cast([asdict(parent) for parent in parents], JSONB),
                    sa.cast(child_payload, JSONB),
                )
            )
        )
        is True
    )


def fail_processing(conn: Connection, run_id: UUID, error_code: str) -> bool:
    """Marca un error de procesamiento y conserva la ejecución ya publicada."""
    return conn.scalar(sa.select(sa.func.app.fail_document_processing(run_id, error_code))) is True


def install_processing(conn: Connection) -> None:
    """Instala las tablas y el acceso acotado del rol de procesamiento."""
    for table in (embedding_spaces, processing_runs, parent_chunks, child_chunks, chunk_embeddings):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")

    conn.exec_driver_sql("ALTER TABLE app.documents ADD COLUMN IF NOT EXISTS active_run_id uuid")
    foreign_keys = sa.inspect(conn).get_foreign_keys("documents", schema="app")
    if not any(key["name"] == "documents_active_run_fk" for key in foreign_keys):
        conn.exec_driver_sql(
            """ALTER TABLE app.documents ADD CONSTRAINT documents_active_run_fk
            FOREIGN KEY (course_id, id, active_run_id)
            REFERENCES app.processing_runs (course_id, document_id, id)"""
        )

    _install_processing_validation(conn)
    _install_processing_operations(conn)


def _install_processing_validation(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.validate_child_offsets() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE source_text text;
        BEGIN
            SELECT extracted_text INTO source_text FROM app.processing_runs WHERE id = NEW.run_id;
            IF source_text IS NULL OR NEW.start_offset < 0 OR NEW.end_offset <= NEW.start_offset
                OR NEW.end_offset > char_length(source_text)
                OR substr(source_text, NEW.start_offset + 1, NEW.end_offset - NEW.start_offset)
                    COLLATE "C" IS DISTINCT FROM NEW.text COLLATE "C" THEN
                RAISE EXCEPTION 'INVALID_CHILD_OFFSETS' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.validate_chunk_embedding() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE expected_dimension integer;
        BEGIN
            SELECT dimension INTO expected_dimension
            FROM app.embedding_spaces WHERE id = NEW.embedding_space_id;
            IF expected_dimension IS NULL OR public.vector_dims(NEW.embedding) <> expected_dimension
                OR abs(public.vector_norm(NEW.embedding) - 1) > 0.00001 THEN
                RAISE EXCEPTION 'INVALID_EMBEDDING' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    for table, trigger, function in (
        ("child_chunks", "child_offsets", "validate_child_offsets"),
        ("chunk_embeddings", "embedding_integrity", "validate_chunk_embedding"),
    ):
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON app.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION app.{function}()"
        )
        conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION app.{function}() FROM PUBLIC")


def _install_processing_operations(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.lock_processing_document(cid uuid, did uuid)
        RETURNS boolean
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            -- Mismo orden que la retirada de documentos: clase, cuenta y documento.
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR UPDATE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM app.users WHERE id = app.course_actor() AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM app.documents
            WHERE course_id = cid AND id = did AND status = 'active' FOR UPDATE;
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.lock_processing_document(uuid, uuid) FROM PUBLIC"
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.start_document_processing(
            cid uuid, did uuid, space_id uuid, config_digest bytea,
            parser text, chunker text, config_parameters jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            original_version uuid;
            run_id uuid := gen_random_uuid();
        BEGIN
            IF NOT app.lock_processing_document(cid, did) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            SELECT id INTO original_version
            FROM app.document_versions
            WHERE course_id = cid AND document_id = did AND version_no = 1 AND status <> 'purged';
            IF NOT FOUND OR NOT EXISTS (
                SELECT FROM app.file_contents WHERE version_id = original_version
            ) THEN
                RETURN NULL;
            END IF;

            IF EXISTS (
                SELECT FROM app.processing_runs AS r
                WHERE r.version_id = original_version AND (
                    r.state = 'running' OR (r.state = 'ready' AND r.config_hash = config_digest)
                )
            ) THEN
                RETURN NULL;
            END IF;
            INSERT INTO app.processing_runs (
                id, course_id, document_id, version_id, embedding_space_id,
                config_hash, parser_version, chunker_version, parameters, state
            ) VALUES (
                run_id, cid, did, original_version, space_id,
                config_digest, parser, chunker, config_parameters, 'running'
            );
            UPDATE app.document_versions SET status = 'processing', error_code = NULL
            WHERE id = original_version AND status <> 'ready';
            RETURN run_id;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.document_processing_source(rid uuid)
        RETURNS TABLE (
            original_name varchar, mime_type varchar, bytes bytea, provider text,
            model_name text, model_revision text, dimension integer, document_prefix text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT v.original_name, v.mime_type, f.bytes, e.provider, e.model_name,
                   e.model_revision, e.dimension, e.document_prefix
            FROM app.processing_runs AS r
            JOIN app.documents AS d ON d.course_id = r.course_id AND d.id = r.document_id
            JOIN app.document_versions AS v ON v.id = r.version_id
            JOIN app.file_contents AS f ON f.version_id = v.id
            JOIN app.embedding_spaces AS e ON e.id = r.embedding_space_id
            JOIN app.courses AS c ON c.id = r.course_id
            WHERE r.id = rid AND r.state = 'running' AND d.status = 'active'
              AND c.status = 'active' AND v.status <> 'purged'
              AND app.has_course_role(r.course_id, ARRAY['student', 'coordinator'])
        $$"""
    )
    _install_processing_publication(conn)
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.fail_document_processing(rid uuid, failure_code text)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE run app.processing_runs;
        BEGIN
            SELECT * INTO run FROM app.processing_runs WHERE id = rid AND state = 'running';
            IF NOT FOUND OR NOT app.lock_processing_document(run.course_id, run.document_id) THEN
                RETURN false;
            END IF;
            IF failure_code IS NULL OR failure_code !~ '^[A-Z0-9_]{1,80}$' THEN
                RAISE EXCEPTION 'INVALID_PROCESSING_ERROR_CODE' USING ERRCODE = '22023';
            END IF;

            UPDATE app.processing_runs
            SET state = 'failed', finished_at = clock_timestamp(), error_code = failure_code
            WHERE id = rid AND state = 'running';
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            UPDATE app.document_versions SET status = 'failed', error_code = failure_code
            WHERE id = run.version_id AND status = 'processing';
            RETURN true;
        END
        $$"""
    )
    entrypoints = (
        "app.start_document_processing(uuid, uuid, uuid, bytea, text, text, jsonb), "
        "app.document_processing_source(uuid), "
        "app.publish_document_processing(uuid, text, jsonb, jsonb), "
        "app.fail_document_processing(uuid, text)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {entrypoints} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {entrypoints} TO tutoria_worker")


def _install_processing_publication(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.publish_document_processing(
            rid uuid, source_text text, parent_payload jsonb, child_payload jsonb
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            run app.processing_runs;
            inserted bigint;
        BEGIN
            SELECT * INTO run FROM app.processing_runs WHERE id = rid AND state = 'running';
            IF NOT FOUND OR NOT app.lock_processing_document(run.course_id, run.document_id) THEN
                RETURN false;
            END IF;
            IF NOT EXISTS (SELECT FROM app.processing_runs WHERE id = rid AND state = 'running')
                OR NOT EXISTS (SELECT FROM app.document_versions WHERE id = run.version_id AND status <> 'purged') THEN
                RETURN false;
            END IF;
            IF source_text IS NULL OR length(source_text) = 0
                OR jsonb_array_length(parent_payload) = 0 OR jsonb_array_length(child_payload) = 0
                OR parent_payload IS NULL OR child_payload IS NULL THEN
                RAISE EXCEPTION 'EMPTY_PROCESSING_RESULT' USING ERRCODE = '23514';
            END IF;

            -- Los triggers de offsets consultan el texto de esta misma transacción.
            UPDATE app.processing_runs SET state = 'ready', extracted_text = source_text,
                finished_at = clock_timestamp()
            WHERE id = rid;
            INSERT INTO app.parent_chunks (
                id, course_id, document_id, run_id, ordinal, section_path, text, locator, token_estimate
            )
            SELECT gen_random_uuid(), run.course_id, run.document_id, rid, p.n - 1,
                   ARRAY(SELECT jsonb_array_elements_text(p.value -> 'section_path')),
                   p.value ->> 'text', p.value -> 'locator', (p.value ->> 'token_estimate')::int
            FROM jsonb_array_elements(parent_payload) WITH ORDINALITY AS p(value, n);

            INSERT INTO app.child_chunks (
                id, course_id, document_id, run_id, parent_id, ordinal, text, embedding_text,
                start_offset, end_offset, token_estimate
            )
            SELECT gen_random_uuid(), run.course_id, run.document_id, rid, p.id, c.n - 1,
                   c.value ->> 'text', c.value ->> 'embedding_text',
                   (c.value ->> 'start_offset')::int, (c.value ->> 'end_offset')::int,
                   (c.value ->> 'token_estimate')::int
            FROM jsonb_array_elements(child_payload) WITH ORDINALITY AS c(value, n)
            JOIN app.parent_chunks AS p
              ON p.run_id = rid AND p.ordinal = (c.value ->> 'parent_index')::int;
            GET DIAGNOSTICS inserted = ROW_COUNT;
            IF inserted <> jsonb_array_length(child_payload) OR EXISTS (
                SELECT FROM app.parent_chunks AS p WHERE p.run_id = rid
                AND NOT EXISTS (SELECT FROM app.child_chunks AS c WHERE c.parent_id = p.id)
            ) THEN
                RAISE EXCEPTION 'INVALID_CHUNK_PARENT' USING ERRCODE = '23514';
            END IF;

            INSERT INTO app.chunk_embeddings (course_id, run_id, child_id, embedding_space_id, embedding)
            SELECT run.course_id, rid, c.id, run.embedding_space_id,
                   (item.value ->> 'embedding')::public.vector
            FROM jsonb_array_elements(child_payload) WITH ORDINALITY AS item(value, n)
            JOIN app.child_chunks AS c ON c.run_id = rid AND c.ordinal = item.n - 1;

            UPDATE app.document_versions SET status = 'ready', error_code = NULL
            WHERE id = run.version_id;
            UPDATE app.documents SET active_run_id = rid, updated_at = clock_timestamp(),
                row_version = row_version + 1
            WHERE course_id = run.course_id AND id = run.document_id;
            UPDATE app.courses SET corpus_revision = corpus_revision + 1 WHERE id = run.course_id;
            RETURN true;
        END
        $$"""
    )
