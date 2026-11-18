"""Catálogo compartido de conceptos y prerrequisitos por clase."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping

from .processing import parent_chunks

metadata = parent_chunks.metadata
concepts = sa.Table(
    "concepts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "course_id", sa.Uuid, sa.ForeignKey("app.courses.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("label", sa.String(255), nullable=False),
    sa.Column(
        "canonical_key",
        sa.Text,
        sa.Computed(
            "lower(normalize(btrim(regexp_replace(label, '[[:space:]]+', ' ', 'g'))) "
            "COLLATE pg_catalog.pg_unicode_fast)"
        ),
        nullable=False,
    ),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.CheckConstraint("length(canonical_key) > 0", name="concepts_label_not_blank"),
    sa.UniqueConstraint("course_id", "id"),
    sa.UniqueConstraint("course_id", "canonical_key"),
)
concept_aliases = sa.Table(
    "concept_aliases",
    metadata,
    sa.Column("course_id", sa.Uuid, primary_key=True),
    sa.Column("concept_id", sa.Uuid, primary_key=True),
    sa.Column("alias", sa.String(255), nullable=False),
    sa.Column(
        "normalized_alias",
        sa.Text,
        sa.Computed(
            "lower(normalize(btrim(regexp_replace(alias, '[[:space:]]+', ' ', 'g'))) "
            "COLLATE pg_catalog.pg_unicode_fast)"
        ),
        primary_key=True,
    ),
    sa.CheckConstraint("length(normalized_alias) > 0", name="concept_aliases_not_blank"),
    sa.ForeignKeyConstraint(
        ["course_id", "concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
)
concept_edges = sa.Table(
    "concept_edges",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("source_id", sa.Uuid, nullable=False),
    sa.Column("target_id", sa.Uuid, nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "source_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "target_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("source_id <> target_id", name="prerequisite_distinct_concepts"),
    sa.UniqueConstraint("course_id", "id"),
    sa.UniqueConstraint("course_id", "source_id", "target_id"),
)
sa.Index("concept_edges_target_idx", concept_edges.c.course_id, concept_edges.c.target_id)


def _source_table(name: str, subject: str, target: str) -> sa.Table:
    table = sa.Table(
        name,
        metadata,
        sa.Column("course_id", sa.Uuid, primary_key=True),
        sa.Column(subject, sa.Uuid, primary_key=True),
        sa.Column("parent_id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, nullable=False),
        sa.Column("run_id", sa.Uuid, nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id", subject],
            [f"app.{target}.course_id", f"app.{target}.id"],
            ondelete="CASCADE",
        ),
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
    )
    sa.Index(
        f"{name}_parent_idx",
        table.c.course_id,
        table.c.document_id,
        table.c.run_id,
        table.c.parent_id,
    )
    return table


concept_sources = _source_table("concept_sources", "concept_id", "concepts")
edge_sources = _source_table("edge_sources", "edge_id", "concept_edges")
_graph_revisions = sa.Table(
    "catalog_graph_revisions",
    metadata,
    sa.Column(
        "course_id", sa.Uuid, sa.ForeignKey("app.courses.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("revision", sa.BigInteger, server_default="1", nullable=False),
)


def create_concept(conn: Connection, course_id: UUID, *, label: str, description: str = "") -> UUID:
    return conn.execute(
        sa.insert(concepts)
        .values(id=uuid4(), course_id=course_id, label=label, description=description)
        .returning(concepts.c.id)
    ).scalar_one()


def list_concepts(conn: Connection, course_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(concepts)
        .where(concepts.c.course_id == course_id)
        .order_by(concepts.c.canonical_key)
    )
    return list(conn.execute(stmt).mappings())


def add_concept_alias(conn: Connection, course_id: UUID, concept_id: UUID, alias: str) -> str:
    return conn.execute(
        sa.insert(concept_aliases)
        .values(course_id=course_id, concept_id=concept_id, alias=alias)
        .returning(concept_aliases.c.normalized_alias)
    ).scalar_one()


def has_concept(conn: Connection, course_id: UUID, concept_id: UUID) -> bool:
    stmt = sa.select(concepts.c.id).where(
        concepts.c.course_id == course_id, concepts.c.id == concept_id
    )
    return conn.scalar(stmt) is not None


def list_course_edges(conn: Connection, course_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(concept_edges)
        .where(concept_edges.c.course_id == course_id)
        .order_by(concept_edges.c.source_id, concept_edges.c.target_id)
    )
    return list(conn.execute(stmt).mappings())


def list_concept_aliases(conn: Connection, course_id: UUID, concept_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(concept_aliases)
        .where(concept_aliases.c.course_id == course_id, concept_aliases.c.concept_id == concept_id)
        .order_by(concept_aliases.c.normalized_alias)
    )
    return list(conn.execute(stmt).mappings())


def add_prerequisite(
    conn: Connection, course_id: UUID, prerequisite_id: UUID, concept_id: UUID
) -> UUID:
    """Registra que el concepto requiere conocer primero el prerrequisito."""
    return conn.execute(
        sa.insert(concept_edges)
        .values(id=uuid4(), course_id=course_id, source_id=prerequisite_id, target_id=concept_id)
        .returning(concept_edges.c.id)
    ).scalar_one()


def list_prerequisites(conn: Connection, course_id: UUID, concept_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(concept_edges)
        .where(concept_edges.c.course_id == course_id, concept_edges.c.target_id == concept_id)
        .order_by(concept_edges.c.source_id)
    )
    return list(conn.execute(stmt).mappings())


def add_concept_source(
    conn: Connection, course_id: UUID, concept_id: UUID, parent_id: UUID
) -> None:
    conn.execute(
        sa.insert(concept_sources).values(
            course_id=course_id, concept_id=concept_id, parent_id=parent_id
        )
    )


def add_prerequisite_source(
    conn: Connection, course_id: UUID, edge_id: UUID, parent_id: UUID
) -> None:
    conn.execute(
        sa.insert(edge_sources).values(course_id=course_id, edge_id=edge_id, parent_id=parent_id)
    )


def list_concept_sources(conn: Connection, course_id: UUID, concept_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(concept_sources)
        .where(concept_sources.c.course_id == course_id, concept_sources.c.concept_id == concept_id)
        .order_by(concept_sources.c.parent_id)
    )
    return list(conn.execute(stmt).mappings())


def list_prerequisite_sources(conn: Connection, course_id: UUID, edge_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(edge_sources)
        .where(edge_sources.c.course_id == course_id, edge_sources.c.edge_id == edge_id)
        .order_by(edge_sources.c.parent_id)
    )
    return list(conn.execute(stmt).mappings())


def install_learning_catalog(conn: Connection) -> None:
    _graph_revisions.create(conn, checkfirst=True)
    for table in (concepts, concept_aliases, concept_edges, concept_sources, edge_sources):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")
        _install_catalog_access(conn, table)
    _install_catalog_guards(conn)


def _install_catalog_access(conn: Connection, table: sa.Table) -> None:
    read = "app.has_course_role(course_id, ARRAY['student', 'coordinator'])"
    if table in (concept_sources, edge_sources):
        read += f""" AND EXISTS (
            SELECT FROM app.documents AS d
            WHERE d.course_id = {table.name}.course_id AND d.id = {table.name}.document_id
              AND d.active_run_id = {table.name}.run_id
        )"""
    conn.exec_driver_sql(f"DROP POLICY IF EXISTS catalog_read ON app.{table.name}")
    conn.exec_driver_sql(
        f"CREATE POLICY catalog_read ON app.{table.name} FOR SELECT TO tutoria_api USING ({read})"
    )
    conn.exec_driver_sql(f"DROP POLICY IF EXISTS catalog_insert ON app.{table.name}")
    conn.exec_driver_sql(
        f"""CREATE POLICY catalog_insert ON app.{table.name} FOR INSERT TO tutoria_api
        WITH CHECK (app.has_course_role(course_id, ARRAY['coordinator']))"""
    )
    conn.exec_driver_sql(f"GRANT SELECT, INSERT ON app.{table.name} TO tutoria_api")


def _install_catalog_guards(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.guard_catalog_insert() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM FROM app.courses WHERE id = NEW.course_id AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(NEW.course_id, ARRAY['coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.guard_prerequisite() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            -- Una escritura por clase serializa el grafo incluso con snapshots repetibles.
            INSERT INTO app.catalog_graph_revisions AS graph (course_id) VALUES (NEW.course_id)
            ON CONFLICT (course_id) DO UPDATE SET revision = graph.revision + 1;
            IF EXISTS (
                WITH RECURSIVE reachable(id) AS (
                    SELECT NEW.target_id
                    UNION
                    SELECT e.target_id FROM app.concept_edges AS e
                    JOIN reachable AS r ON r.id = e.source_id
                    WHERE e.course_id = NEW.course_id
                )
                SELECT FROM reachable WHERE id = NEW.source_id
            ) THEN
                RAISE EXCEPTION 'PREREQUISITE_CYCLE' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.resolve_catalog_source() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            SELECT p.document_id, p.run_id INTO NEW.document_id, NEW.run_id
            FROM app.parent_chunks AS p
            JOIN app.documents AS d ON d.course_id = p.course_id AND d.id = p.document_id
            WHERE p.course_id = NEW.course_id AND p.id = NEW.parent_id
              AND d.status = 'active' AND d.active_run_id = p.run_id
            FOR SHARE OF d;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'INVALID_CATALOG_SOURCE' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    for table in (concepts, concept_aliases, concept_edges, concept_sources, edge_sources):
        _install_insert_trigger(conn, table, "catalog_authorize", "guard_catalog_insert")
    _install_insert_trigger(conn, concept_edges, "catalog_validate", "guard_prerequisite")
    for table in (concept_sources, edge_sources):
        _install_insert_trigger(conn, table, "catalog_validate", "resolve_catalog_source")
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.guard_catalog_insert(), app.guard_prerequisite(), "
        "app.resolve_catalog_source() FROM PUBLIC"
    )


def _install_insert_trigger(conn: Connection, table: sa.Table, trigger: str, function: str) -> None:
    conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger} ON app.{table.name}")
    conn.exec_driver_sql(
        f"CREATE TRIGGER {trigger} BEFORE INSERT ON app.{table.name} "
        f"FOR EACH ROW EXECUTE FUNCTION app.{function}()"
    )
