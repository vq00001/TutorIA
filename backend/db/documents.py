"""Carga y metadatos de documentos independientes, adaptados del ZIP de TutorIA."""

import warnings
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    cast,
    func,
    select,
    text,
    tuple_,
)
from sqlalchemy.engine import Connection, RowMapping

from .courses import courses

documents = Table(
    "documents",
    courses.metadata,
    Column("id", Uuid, primary_key=True),
    Column("course_id", Uuid, ForeignKey("app.courses.id", ondelete="RESTRICT"), nullable=False),
    Column("uploaded_by", Uuid, ForeignKey("app.users.id", ondelete="RESTRICT"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("authors", ARRAY(Text), nullable=False, server_default=text("'{}'")),
    Column("subject", String(150), nullable=False, server_default=""),
    Column("upload_sha256", LargeBinary(32), nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("row_version", BigInteger, nullable=False, server_default=text("1")),
    CheckConstraint("length(btrim(title)) > 0", name="documents_title_present"),
    CheckConstraint("octet_length(upload_sha256) = 32", name="documents_upload_sha256_length"),
    CheckConstraint("status IN ('active', 'deletion_pending')", name="documents_valid_status"),
    UniqueConstraint("course_id", "id", name="documents_course_id_key"),
    UniqueConstraint("course_id", "upload_sha256", name="documents_course_upload_sha256"),
)
Index("documents_course_listing_idx", documents.c.course_id, documents.c.created_at, documents.c.id)

document_versions = Table(
    "document_versions",
    courses.metadata,
    Column("id", Uuid, primary_key=True),
    Column("course_id", Uuid, nullable=False),
    Column("document_id", Uuid, nullable=False),
    Column("version_no", Integer, nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", LargeBinary(32), nullable=False),
    Column("uploaded_by", Uuid, ForeignKey("app.users.id", ondelete="RESTRICT"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("status", Text, nullable=False, server_default="uploaded"),
    Column("error_code", Text),
    ForeignKeyConstraint(
        ["course_id", "document_id"],
        ["app.documents.course_id", "app.documents.id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("document_id", "version_no"),
    UniqueConstraint("course_id", "document_id", "id"),
    CheckConstraint("version_no > 0", name="document_versions_positive_version"),
    CheckConstraint("length(btrim(original_name)) > 0", name="document_versions_name_present"),
    CheckConstraint("length(btrim(mime_type)) > 0", name="document_versions_mime_present"),
    CheckConstraint("size_bytes BETWEEN 1 AND 30000000", name="document_versions_valid_size"),
    CheckConstraint("octet_length(sha256) = 32", name="document_versions_sha256_length"),
    CheckConstraint(
        "status IN ('uploaded', 'processing', 'ready', 'failed', 'purged')",
        name="document_versions_valid_status",
    ),
)

file_contents = Table(
    "file_contents",
    courses.metadata,
    Column("version_id", Uuid, primary_key=True),
    Column("course_id", Uuid, nullable=False),
    Column("document_id", Uuid, nullable=False),
    Column("bytes", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["course_id", "document_id", "version_id"],
        [
            "app.document_versions.course_id",
            "app.document_versions.document_id",
            "app.document_versions.id",
        ],
        ondelete="CASCADE",
    ),
)


class DuplicateDocumentWarning(UserWarning):
    """Se descartó una carga porque los mismos bytes ya existen en la clase."""


def install_documents(conn: Connection) -> None:
    """Instala las tablas, las políticas de lectura y la operación de carga."""
    documents.create(conn, checkfirst=True)
    document_versions.create(conn, checkfirst=True)
    file_contents.create(conn, checkfirst=True)

    for table in (documents, document_versions, file_contents):
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")

    conn.exec_driver_sql("DROP POLICY IF EXISTS visible_documents ON app.documents")
    conn.exec_driver_sql(
        """CREATE POLICY visible_documents ON app.documents FOR SELECT TO tutoria_api
        USING (status = 'active' AND app.has_course_role(course_id, ARRAY['student', 'coordinator']))"""
    )
    conn.exec_driver_sql("DROP POLICY IF EXISTS visible_document_versions ON app.document_versions")
    conn.exec_driver_sql(
        """CREATE POLICY visible_document_versions ON app.document_versions FOR SELECT TO tutoria_api
        USING (
            EXISTS (
                SELECT FROM app.documents AS d
                WHERE d.course_id = document_versions.course_id AND d.id = document_versions.document_id
            )
        )"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.documents, app.document_versions TO tutoria_api")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.register_document(
            cid uuid, filename text, content_type text, payload bytea,
            document_title text, document_subject text, document_description text, document_authors text[]
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            digest bytea;
            doc_id uuid;
            file_id uuid := gen_random_uuid();
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            digest := sha256(payload);
            INSERT INTO app.documents (
                id, course_id, uploaded_by, title, subject, description, authors, upload_sha256
            ) VALUES (
                gen_random_uuid(), cid, actor, document_title, document_subject,
                document_description, document_authors, digest
            )
            ON CONFLICT (course_id, upload_sha256) DO NOTHING
            RETURNING id INTO doc_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            INSERT INTO app.document_versions (
                id, course_id, document_id, version_no, original_name, mime_type, size_bytes, sha256, uploaded_by
            ) VALUES (file_id, cid, doc_id, 1, filename, content_type, octet_length(payload), digest, actor);
            INSERT INTO app.file_contents (version_id, course_id, document_id, bytes)
            VALUES (file_id, cid, doc_id, payload);
            RETURN doc_id;
        END
        $$"""
    )
    signature = "app.register_document(uuid, text, text, bytea, text, text, text, text[])"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")


def register_upload(
    conn: Connection,
    *,
    course_id: UUID,
    filename: str,
    data: bytes,
    mime_type: str,
    subject: str = "",
    title: str | None = None,
    description: str = "",
    authors: list[str] | None = None,
) -> UUID | None:
    """Crea un documento independiente o avisa y devuelve None ante un duplicado."""
    statement = select(
        func.app.register_document(
            course_id,
            filename,
            mime_type,
            data,
            title or filename,
            subject,
            description,
            cast(authors or [], ARRAY(Text)),
        )
    )
    identifier = conn.scalar(statement)
    if identifier is None:
        warnings.warn(
            f"Archivo duplicado descartado: {filename!r}",
            DuplicateDocumentWarning,
            stacklevel=2,
        )
    return identifier


def list_documents(
    conn: Connection,
    course_id: UUID,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None = None,
) -> list[RowMapping]:
    """Lista metadatos de documentos visibles, con cursor por fecha e identificador."""
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = (
        select(
            documents,
            document_versions.c.original_name,
            document_versions.c.mime_type,
            document_versions.c.size_bytes,
            document_versions.c.status.label("processing_status"),
        )
        .join(
            document_versions,
            (document_versions.c.document_id == documents.c.id)
            & (document_versions.c.course_id == documents.c.course_id)
            & (document_versions.c.version_no == 1),
        )
        .where(documents.c.course_id == course_id)
        .order_by(documents.c.created_at, documents.c.id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(tuple_(documents.c.created_at, documents.c.id) > tuple_(*after))
    return list(conn.execute(statement).mappings())
