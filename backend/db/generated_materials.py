"""Materiales privados producidos por una generación, adaptados del ZIP."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from .generations import generation_runs

MaterialKind = Literal["summary", "flashcards", "explanation"]

generated_artifacts = sa.Table(
    "generated_artifacts",
    generation_runs.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "generation_id",
        sa.Uuid,
        sa.ForeignKey("app.generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("content", JSONB, nullable=False),
    sa.Column("schema_version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("kind IN ('summary', 'flashcards', 'explanation')", name="artifacts_kind"),
    sa.CheckConstraint("length(btrim(title)) > 0", name="artifacts_title"),
    sa.CheckConstraint("jsonb_typeof(content) = 'object'", name="artifacts_content"),
    sa.CheckConstraint("schema_version > 0", name="artifacts_schema_version"),
    sa.CheckConstraint("status IN ('active', 'invalidated', 'redacted')", name="artifacts_status"),
    sa.CheckConstraint(
        "status <> 'redacted' OR (title = 'Contenido retirado' AND content = '{}'::jsonb)",
        name="artifacts_redacted_content",
    ),
)
sa.Index("generated_artifacts_generation_idx", generated_artifacts.c.generation_id)


@dataclass(frozen=True)
class GeneratedMaterial:
    title: str
    kind: MaterialKind
    content: dict[str, Any]
    schema_version: int = 1
    id: UUID = field(default_factory=uuid4)


def store_generated_materials(
    conn: Connection,
    course_id: UUID,
    generation_id: UUID,
    materials: Sequence[GeneratedMaterial],
) -> list[UUID]:
    """Guarda un lote; reutilizar un ID con el mismo contenido hace seguro el reintento."""
    payload = [
        {
            "id": str(item.id),
            "kind": item.kind,
            "title": item.title,
            "content": item.content,
            "schema_version": item.schema_version,
        }
        for item in materials
    ]
    statement = sa.select(
        sa.func.app.store_generated_artifacts(
            course_id,
            generation_id,
            sa.cast(payload, JSONB),
        )
    )
    conn.execute(statement)
    return [item.id for item in materials]


def _materials_for_course(course_id: UUID):
    return (
        sa.select(generated_artifacts)
        .join(generation_runs, generation_runs.c.id == generated_artifacts.c.generation_id)
        .where(generation_runs.c.course_id == course_id)
    )


def list_generated_materials(
    conn: Connection,
    course_id: UUID,
    *,
    limit: int = 50,
    before: tuple[datetime, UUID] | None = None,
) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = _materials_for_course(course_id)
    if before is not None:
        statement = statement.where(
            sa.tuple_(generated_artifacts.c.created_at, generated_artifacts.c.id)
            < sa.tuple_(*before)
        )
    statement = statement.order_by(
        generated_artifacts.c.created_at.desc(),
        generated_artifacts.c.id.desc(),
    ).limit(limit)
    return list(conn.execute(statement).mappings())


def get_generated_material(
    conn: Connection,
    course_id: UUID,
    material_id: UUID,
) -> RowMapping | None:
    statement = _materials_for_course(course_id).where(generated_artifacts.c.id == material_id)
    return conn.execute(statement).mappings().one_or_none()


def install_generated_materials(conn: Connection) -> None:
    generated_artifacts.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.generated_artifacts ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.generated_artifacts FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS own_artifacts_read ON app.generated_artifacts")
    conn.exec_driver_sql(
        """CREATE POLICY own_artifacts_read ON app.generated_artifacts FOR SELECT TO tutoria_api
        USING (
            EXISTS (SELECT FROM app.generation_runs AS g WHERE g.id = generated_artifacts.generation_id)
        )"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.generated_artifacts TO tutoria_api")
    _install_material_writes(conn)
    _install_material_redaction(conn)


def _install_material_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.store_generated_artifacts(cid uuid, gid uuid, payload jsonb)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE actor uuid := app.course_actor();
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            PERFORM 1 FROM app.generation_runs WHERE id = gid AND course_id = cid AND user_id = actor FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT FROM app.generation_runs AS g
                WHERE g.id = gid AND g.status IN ('running', 'completed')
            ) OR NOT EXISTS (
                SELECT FROM app.generation_requests AS r WHERE r.generation_id = gid
            ) THEN
                RAISE EXCEPTION 'GENERATION_NOT_AVAILABLE' USING ERRCODE = '22023';
            END IF;

            IF jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'INVALID_GENERATED_MATERIALS' USING ERRCODE = '23514';
            END IF;
            IF jsonb_array_length(payload) = 0 OR (
                SELECT count(DISTINCT (item.value ->> 'id')::uuid)
                FROM jsonb_array_elements(payload) AS item(value)
            ) <> jsonb_array_length(payload) THEN
                RAISE EXCEPTION 'INVALID_GENERATED_MATERIALS' USING ERRCODE = '23514';
            END IF;

            INSERT INTO app.generated_artifacts (id, generation_id, kind, title, content, schema_version)
            SELECT (item.value ->> 'id')::uuid, gid, item.value ->> 'kind',
                   item.value ->> 'title', item.value -> 'content', (item.value ->> 'schema_version')::integer
            FROM jsonb_array_elements(payload) AS item(value)
            ON CONFLICT (id) DO NOTHING;

            IF EXISTS (
                SELECT FROM jsonb_array_elements(payload) AS item(value)
                WHERE NOT EXISTS (
                    SELECT FROM app.generated_artifacts AS a
                    WHERE a.id = (item.value ->> 'id')::uuid AND a.generation_id = gid
                      AND a.kind = item.value ->> 'kind' AND a.title = item.value ->> 'title'
                      AND a.content = item.value -> 'content'
                      AND a.schema_version = (item.value ->> 'schema_version')::integer
                      AND a.status = 'active'
                )
            ) THEN
                RAISE EXCEPTION 'GENERATED_MATERIAL_CONFLICT' USING ERRCODE = '23505';
            END IF;
        END
        $$"""
    )
    signature = "app.store_generated_artifacts(uuid, uuid, jsonb)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")


def _install_material_redaction(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.redact_generation_artifacts() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.generated_artifacts SET status = 'redacted',
                title = 'Contenido retirado', content = '{}'::jsonb
            WHERE generation_id = NEW.id AND status <> 'redacted';
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "DROP TRIGGER IF EXISTS redact_generation_artifacts ON app.generation_runs"
    )
    conn.exec_driver_sql(
        """CREATE TRIGGER redact_generation_artifacts AFTER UPDATE OF status ON app.generation_runs
        FOR EACH ROW WHEN (NEW.status = 'invalidated' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.redact_generation_artifacts()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.redact_generation_artifacts() FROM PUBLIC")
