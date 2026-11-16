"""Extensión privada del grafo de aprendizaje de cada alumno."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping

from .learning_catalog import concepts

metadata = concepts.metadata

personal_nodes = sa.Table(
    "personal_graph_nodes",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("label", sa.String(255), nullable=False),
    sa.Column(
        "canonical_key", sa.Text,
        sa.Computed(
            "lower(normalize(btrim(regexp_replace(label, '[[:space:]]+', ' ', 'g'))) "
            "COLLATE pg_catalog.pg_unicode_fast)"
        ), nullable=False,
    ),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("course_id", "user_id", "id"),
    sa.UniqueConstraint("course_id", "user_id", "canonical_key"),
    sa.CheckConstraint("length(canonical_key) > 0", name="personal_graph_node_label_present"),
)

personal_recommendations = sa.Table(
    "personal_graph_recommendations",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("target_concept_id", sa.Uuid, nullable=False),
    sa.Column("existing_concept_id", sa.Uuid),
    sa.Column("personal_node_id", sa.Uuid),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "target_concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "existing_concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "personal_node_id"],
        ["app.personal_graph_nodes.course_id", "app.personal_graph_nodes.user_id",
         "app.personal_graph_nodes.id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "(existing_concept_id IS NOT NULL) <> (personal_node_id IS NOT NULL)",
        name="personal_graph_one_source",
    ),
    sa.CheckConstraint(
        "existing_concept_id IS NULL OR existing_concept_id <> target_concept_id",
        name="personal_graph_distinct_concepts",
    ),
    sa.CheckConstraint("length(btrim(reason)) > 0", name="personal_graph_reason_present"),
)
sa.Index(
    "personal_graph_existing_pair", personal_recommendations.c.course_id,
    personal_recommendations.c.user_id, personal_recommendations.c.target_concept_id,
    personal_recommendations.c.existing_concept_id, unique=True,
    postgresql_where=personal_recommendations.c.existing_concept_id.is_not(None),
)
sa.Index(
    "personal_graph_new_pair", personal_recommendations.c.course_id,
    personal_recommendations.c.user_id, personal_recommendations.c.target_concept_id,
    personal_recommendations.c.personal_node_id, unique=True,
    postgresql_where=personal_recommendations.c.personal_node_id.is_not(None),
)


def create_personal_node(
    conn: Connection, course_id: UUID, user_id: UUID, *, label: str, description: str
) -> UUID:
    return conn.execute(sa.insert(personal_nodes).values(
        id=uuid4(), course_id=course_id, user_id=user_id,
        label=label, description=description,
    ).returning(personal_nodes.c.id)).scalar_one()


def create_personal_recommendation(
    conn: Connection, course_id: UUID, user_id: UUID, *, target_concept_id: UUID,
    existing_concept_id: UUID | None, personal_node_id: UUID | None, reason: str,
) -> UUID:
    return conn.execute(sa.insert(personal_recommendations).values(
        id=uuid4(), course_id=course_id, user_id=user_id,
        target_concept_id=target_concept_id, existing_concept_id=existing_concept_id,
        personal_node_id=personal_node_id, reason=reason,
    ).returning(personal_recommendations.c.id)).scalar_one()


def list_personal_nodes(conn: Connection, course_id: UUID) -> list[RowMapping]:
    return list(conn.execute(sa.select(personal_nodes).where(
        personal_nodes.c.course_id == course_id
    ).order_by(personal_nodes.c.canonical_key)).mappings())


def list_personal_recommendations(conn: Connection, course_id: UUID) -> list[RowMapping]:
    return list(conn.execute(sa.select(personal_recommendations).where(
        personal_recommendations.c.course_id == course_id
    ).order_by(personal_recommendations.c.created_at, personal_recommendations.c.id)).mappings())


def install_personal_graph(conn: Connection) -> None:
    for table in (personal_nodes, personal_recommendations):
        name = table.name
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{name} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS personal_graph_read ON app.{name}")
        conn.exec_driver_sql(f"""CREATE POLICY personal_graph_read ON app.{name}
            FOR SELECT TO tutoria_api USING (
                user_id = app.course_actor()
                AND app.has_course_role(course_id, ARRAY['student'])
            )""")
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS personal_graph_insert ON app.{name}")
        conn.exec_driver_sql(f"""CREATE POLICY personal_graph_insert ON app.{name}
            FOR INSERT TO tutoria_api WITH CHECK (
                user_id = app.course_actor()
                AND app.has_course_role(course_id, ARRAY['student'])
                AND EXISTS (SELECT FROM app.courses AS c
                            WHERE c.id = course_id AND c.status = 'active')
            )""")
        conn.exec_driver_sql(f"GRANT SELECT, INSERT ON app.{name} TO tutoria_api")
