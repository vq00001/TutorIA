"""Evidencia de exámenes y dominio beta_v1, adaptados del ZIP de TutorIA."""

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping

from .exam_attempts import exam_attempts, grades
from .learning_catalog import concepts

# La clave del examen elige un solo intento, incluso con transacciones concurrentes.
_evidence_attempts = sa.Table(
    "mastery_exam_attempts",
    exam_attempts.metadata,
    sa.Column("exam_id", sa.Uuid, primary_key=True),
    sa.Column("attempt_id", sa.Uuid, nullable=False),
    sa.ForeignKeyConstraint(
        ["exam_id", "attempt_id"],
        ["app.exam_attempts.exam_id", "app.exam_attempts.id"],
        ondelete="CASCADE",
    ),
)

mastery_events = sa.Table(
    "mastery_events",
    concepts.metadata,
    sa.Column(
        "grade_id",
        sa.Uuid,
        sa.ForeignKey(grades.c.answer_id, ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("course_id", sa.Uuid, nullable=False),
    sa.Column("user_id", sa.Uuid, nullable=False),
    sa.Column("concept_id", sa.Uuid, nullable=False),
    sa.Column("value", sa.Numeric, nullable=False),
    sa.Column("weight", sa.Numeric, nullable=False),
    sa.Column("algorithm_version", sa.Text, server_default="beta_v1", nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "concept_id"],
        ["app.concepts.course_id", "app.concepts.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["course_id", "user_id"],
        ["app.memberships.course_id", "app.memberships.user_id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("value BETWEEN 0 AND 1 AND weight > 0", name="mastery_event_bounds"),
)
sa.Index(
    "mastery_events_owner_idx",
    mastery_events.c.course_id,
    mastery_events.c.user_id,
    mastery_events.c.concept_id,
)


def _own_events(course_id: UUID):
    return (
        mastery_events.c.course_id == course_id,
        mastery_events.c.user_id == sa.func.app.course_actor(),
    )


def list_mastery_evidence(
    conn: Connection, course_id: UUID, concept_id: UUID, *, limit: int = 50
) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    stmt = (
        sa.select(mastery_events)
        .where(*_own_events(course_id), mastery_events.c.concept_id == concept_id)
        .order_by(mastery_events.c.created_at.desc(), mastery_events.c.grade_id)
        .limit(limit)
    )
    return list(conn.execute(stmt).mappings())


def list_concept_mastery(conn: Connection, course_id: UUID) -> list[RowMapping]:
    """Calcula el dominio de cada concepto visible para el usuario de la transacción."""
    return list(conn.execute(concept_mastery_statement(course_id)).mappings())


def concept_mastery_statement(course_id: UUID):
    evidence = (
        sa.select(
            mastery_events.c.concept_id,
            sa.func.count().label("count"),
            sa.func.sum(mastery_events.c.weight * mastery_events.c.value).label("positive"),
            sa.func.sum(mastery_events.c.weight * (1 - mastery_events.c.value)).label("negative"),
        )
        .where(*_own_events(course_id), mastery_events.c.algorithm_version == "beta_v1")
        .group_by(mastery_events.c.concept_id)
        .subquery()
    )
    alpha = 1 + sa.func.coalesce(evidence.c.positive, 0)
    beta = 1 + sa.func.coalesce(evidence.c.negative, 0)
    score = alpha / (alpha + beta)
    level = sa.case(
        (evidence.c.count.is_(None), "unknown"),
        (sa.and_(evidence.c.count >= 3, alpha >= Decimal("0.8") * (alpha + beta)), "mastered"),
        (alpha < Decimal("0.6") * (alpha + beta), "reinforce"),
        else_="developing",
    )
    return (
        sa.select(
            concepts.c.id.label("concept_id"),
            concepts.c.label,
            sa.func.coalesce(evidence.c.count, 0).label("evidence_count"),
            alpha.label("alpha"),
            beta.label("beta"),
            score.label("score"),
            level.label("level"),
        )
        .outerjoin(evidence, evidence.c.concept_id == concepts.c.id)
        .where(concepts.c.course_id == course_id)
        .order_by(concepts.c.canonical_key)
    )


def install_learning_progress(conn: Connection) -> None:
    _evidence_attempts.create(conn, checkfirst=True)
    mastery_events.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.mastery_events ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.mastery_events FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS mastery_events_read ON app.mastery_events")
    conn.exec_driver_sql(
        """CREATE POLICY mastery_events_read ON app.mastery_events FOR SELECT TO tutoria_api
        USING (user_id = app.course_actor()
            AND app.has_course_role(course_id, ARRAY['student', 'coordinator']))"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.mastery_events TO tutoria_api")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.record_exam_mastery() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            INSERT INTO app.mastery_exam_attempts (exam_id, attempt_id)
            VALUES (NEW.exam_id, NEW.id)
            ON CONFLICT (exam_id) DO NOTHING;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            INSERT INTO app.mastery_events (grade_id, course_id, user_id, concept_id, value, weight)
            SELECT g.answer_id, r.course_id, r.user_id, q.concept_id, g.points / q.max_points, 1
            FROM app.answers AS a
            JOIN app.grades AS g ON g.answer_id = a.id
            JOIN app.questions AS q ON q.id = a.question_id
            JOIN app.exams AS e ON e.id = a.exam_id
            JOIN app.generation_runs AS r ON r.id = e.generation_id
            JOIN app.concepts AS c ON c.course_id = r.course_id AND c.id = q.concept_id
            WHERE a.attempt_id = NEW.id AND g.status = 'accepted' AND g.method = 'deterministic'
            ON CONFLICT (grade_id) DO NOTHING;
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.record_exam_mastery() FROM PUBLIC")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS record_exam_mastery ON app.exam_attempts")
    conn.exec_driver_sql(
        """CREATE TRIGGER record_exam_mastery AFTER UPDATE OF status ON app.exam_attempts
        FOR EACH ROW WHEN (NEW.status = 'graded' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.record_exam_mastery()"""
    )
