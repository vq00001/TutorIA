"""Metas personales y rutas de estudio, adaptadas del ZIP de TutorIA."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Connection, RowMapping

from .learning_catalog import concepts
from .learning_progress import concept_mastery_statement

metadata = concepts.metadata
learning_goals = sa.Table(
    "learning_goals",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("target_date", sa.Date),
    sa.Column("status", sa.Text, server_default="active", nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("course_id", "user_id", "id"),
    sa.CheckConstraint("title ~ '[^[:space:]]'", name="learning_goal_title"),
    sa.CheckConstraint(
        "status IN ('active', 'completed', 'archived')", name="learning_goal_status"
    ),
)
goal_concepts = sa.Table(
    "goal_concepts",
    metadata,
    sa.Column("goal_id", sa.Uuid, primary_key=True),
    sa.Column("concept_id", sa.Uuid, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "goal_id"],
        ["app.learning_goals.course_id", "app.learning_goals.user_id", "app.learning_goals.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
    ),
)
roadmaps = sa.Table(
    "roadmaps",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("goal_id", sa.Uuid, nullable=False),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("version_no", sa.Integer, nullable=False),
    sa.Column("graph_revision", sa.BigInteger, nullable=False),
    sa.Column("algorithm_version", sa.Text, server_default="toposort_v1", nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "goal_id"],
        ["app.learning_goals.course_id", "app.learning_goals.user_id", "app.learning_goals.id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("goal_id", "version_no"),
    sa.UniqueConstraint("course_id", "user_id", "id"),
    sa.CheckConstraint("version_no > 0 AND graph_revision >= 0", name="roadmap_version_bounds"),
)
roadmap_steps = sa.Table(
    "roadmap_steps",
    metadata,
    sa.Column("roadmap_id", sa.Uuid, primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("concept_id", sa.Uuid, nullable=False),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id", "roadmap_id"],
        ["app.roadmaps.course_id", "app.roadmaps.user_id", "app.roadmaps.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
    ),
    sa.UniqueConstraint("roadmap_id", "concept_id"),
    sa.CheckConstraint("position > 0", name="roadmap_step_position"),
)


def create_learning_goal(
    conn: Connection,
    course_id: UUID,
    *,
    title: str,
    concept_ids: Sequence[UUID],
    target_date: date | None = None,
) -> UUID:
    stmt = sa.select(
        sa.func.app.create_learning_goal(
            course_id,
            title,
            sa.cast(list(concept_ids), ARRAY(sa.Uuid)),
            sa.cast(target_date, sa.Date),
        )
    )
    return conn.execute(stmt).scalar_one()


def list_learning_goals(conn: Connection, course_id: UUID) -> list[RowMapping]:
    targets = (
        sa.select(sa.func.array_agg(goal_concepts.c.concept_id))
        .where(goal_concepts.c.goal_id == learning_goals.c.id)
        .scalar_subquery()
    )
    stmt = (
        sa.select(learning_goals, targets.label("concept_ids"))
        .where(learning_goals.c.course_id == course_id)
        .order_by(learning_goals.c.created_at.desc(), learning_goals.c.id)
    )
    return list(conn.execute(stmt).mappings())


def set_learning_goal_status(conn: Connection, course_id: UUID, goal_id: UUID, status: str) -> UUID:
    return conn.execute(
        sa.select(sa.func.app.set_learning_goal_status(course_id, goal_id, status))
    ).scalar_one()


def build_roadmap(conn: Connection, course_id: UUID, goal_id: UUID) -> UUID:
    return conn.execute(
        sa.select(sa.func.app.build_learning_roadmap(course_id, goal_id))
    ).scalar_one()


def list_roadmaps(conn: Connection, course_id: UUID, goal_id: UUID) -> list[RowMapping]:
    stmt = (
        sa.select(roadmaps)
        .where(roadmaps.c.course_id == course_id, roadmaps.c.goal_id == goal_id)
        .order_by(roadmaps.c.version_no.desc())
    )
    return list(conn.execute(stmt).mappings())


def has_learning_goal(conn: Connection, course_id: UUID, goal_id: UUID) -> bool:
    stmt = sa.select(learning_goals.c.id).where(
        learning_goals.c.course_id == course_id, learning_goals.c.id == goal_id
    )
    return conn.scalar(stmt) is not None


def get_roadmap(
    conn: Connection, course_id: UUID, goal_id: UUID, roadmap_id: UUID
) -> RowMapping | None:
    stmt = sa.select(roadmaps).where(
        roadmaps.c.course_id == course_id, roadmaps.c.goal_id == goal_id, roadmaps.c.id == roadmap_id
    )
    return conn.execute(stmt).mappings().one_or_none()


def list_roadmap_steps(conn: Connection, course_id: UUID, roadmap_id: UUID) -> list[RowMapping]:
    mastery = concept_mastery_statement(course_id).order_by(None).subquery()
    stmt = (
        sa.select(roadmap_steps, mastery.c.label, mastery.c.level.label("mastery_level"))
        .join(mastery, mastery.c.concept_id == roadmap_steps.c.concept_id)
        .where(roadmap_steps.c.course_id == course_id, roadmap_steps.c.roadmap_id == roadmap_id)
        .order_by(roadmap_steps.c.position)
    )
    return list(conn.execute(stmt).mappings())


def install_private_learning_tables(conn: Connection, *tables: sa.Table) -> None:
    for table in tables:
        name = table.name
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{name} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS own_learning_read ON app.{name}")
        conn.exec_driver_sql(f"""CREATE POLICY own_learning_read ON app.{name}
            FOR SELECT TO tutoria_api USING (
                user_id = app.course_actor()
                AND app.has_course_role(course_id, ARRAY['student', 'coordinator'])
            )""")
        conn.exec_driver_sql(f"GRANT SELECT ON app.{name} TO tutoria_api")


def install_learning_plans(conn: Connection) -> None:
    install_private_learning_tables(conn, learning_goals, goal_concepts, roadmaps, roadmap_steps)
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.lock_learning_course(cid uuid) RETURNS uuid
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
        DECLARE actor uuid := app.course_actor();
        BEGIN
            PERFORM FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            RETURN actor;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.lock_learning_course(uuid) FROM PUBLIC")
    _install_goal_writes(conn)
    _install_roadmap_write(conn)


def _install_goal_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.create_learning_goal(
            cid uuid, goal_title text, targets uuid[], due date
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
            actor uuid := app.lock_learning_course(cid);
            gid uuid := gen_random_uuid();
        BEGIN
            IF coalesce(cardinality(targets), 0) = 0 OR EXISTS (
                SELECT FROM unnest(targets) AS target(id) WHERE NOT EXISTS (
                    SELECT FROM app.concepts AS c WHERE c.course_id = cid AND c.id = target.id
                )
            ) THEN
                RAISE EXCEPTION 'INVALID_GOAL_CONCEPTS' USING ERRCODE = '23514';
            END IF;
            INSERT INTO app.learning_goals (id, course_id, user_id, title, target_date)
            VALUES (gid, cid, actor, goal_title, due);
            INSERT INTO app.goal_concepts (goal_id, concept_id, course_id, user_id)
            SELECT DISTINCT gid, target.id, cid, actor FROM unnest(targets) AS target(id);
            RETURN gid;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.set_learning_goal_status(
            cid uuid, gid uuid, next_status text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
            actor uuid := app.lock_learning_course(cid);
            current_status text;
        BEGIN
            SELECT status INTO current_status FROM app.learning_goals
            WHERE id = gid AND course_id = cid AND user_id = actor FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF current_status = next_status THEN
                RETURN gid;
            END IF;
            IF next_status IS NULL OR NOT (
                (current_status = 'active' AND next_status IN ('completed', 'archived'))
                OR (current_status = 'completed' AND next_status = 'archived')
            ) THEN
                RAISE EXCEPTION 'INVALID_GOAL_TRANSITION' USING ERRCODE = '23514';
            END IF;
            UPDATE app.learning_goals SET status = next_status WHERE id = gid;
            RETURN gid;
        END
        $$"""
    )
    signatures = "app.create_learning_goal(uuid, text, uuid[], date), app.set_learning_goal_status(uuid, uuid, text)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")


def _install_roadmap_write(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.build_learning_roadmap(cid uuid, gid uuid)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
            actor uuid := app.lock_learning_course(cid);
            goal_status text;
            latest app.roadmaps;
            revision bigint;
            pending uuid[];
            ordered uuid[] := '{}';
            candidate uuid;
            rid uuid := gen_random_uuid();
        BEGIN
            SELECT status INTO goal_status FROM app.learning_goals
            WHERE id = gid AND course_id = cid AND user_id = actor FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF goal_status <> 'active' THEN
                RAISE EXCEPTION 'GOAL_INACTIVE' USING ERRCODE = '23514';
            END IF;

            -- El catálogo escribe esta fila antes de cambiar las aristas.
            INSERT INTO app.catalog_graph_revisions (course_id, revision) VALUES (cid, 0)
            ON CONFLICT (course_id) DO NOTHING;
            SELECT graph.revision INTO revision
            FROM app.catalog_graph_revisions AS graph WHERE graph.course_id = cid FOR SHARE;
            SELECT * INTO latest FROM app.roadmaps
            WHERE goal_id = gid ORDER BY version_no DESC LIMIT 1;
            IF latest.graph_revision = revision THEN
                RETURN latest.id;
            END IF;

            WITH RECURSIVE required(id) AS (
                SELECT concept_id FROM app.goal_concepts WHERE goal_id = gid
                UNION
                SELECT e.source_id FROM app.concept_edges AS e
                JOIN required AS r ON e.target_id = r.id WHERE e.course_id = cid
            )
            SELECT array_agg(id) INTO pending FROM required;
            IF pending IS NULL THEN
                RAISE EXCEPTION 'INVALID_GOAL_CONCEPTS' USING ERRCODE = '23514';
            END IF;

            WHILE cardinality(pending) > 0 LOOP
                SELECT c.id INTO candidate FROM app.concepts AS c
                WHERE c.course_id = cid AND c.id = ANY(pending) AND NOT EXISTS (
                    SELECT FROM app.concept_edges AS e WHERE e.course_id = cid
                      AND e.target_id = c.id AND e.source_id = ANY(pending)
                )
                ORDER BY c.canonical_key, c.id LIMIT 1;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'INVALID_PREREQUISITE_GRAPH' USING ERRCODE = '23514';
                END IF;
                ordered := array_append(ordered, candidate);
                pending := array_remove(pending, candidate);
            END LOOP;

            INSERT INTO app.roadmaps (id, goal_id, course_id, user_id, version_no, graph_revision)
            VALUES (rid, gid, cid, actor, coalesce(latest.version_no, 0) + 1, revision);
            INSERT INTO app.roadmap_steps (roadmap_id, position, course_id, user_id, concept_id)
            SELECT rid, step.position, cid, actor, step.concept_id
            FROM unnest(ordered) WITH ORDINALITY AS step(concept_id, position);
            RETURN rid;
        END
        $$"""
    )
    signature = "app.build_learning_roadmap(uuid, uuid)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")
