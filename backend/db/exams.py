"""Exámenes generados y claves privadas, adaptados del ZIP de TutorIA."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.engine import Connection, RowMapping

from .generations import generation_runs

ExamKind = Literal["diagnostic", "practice"]
QuestionKind = Literal["alternatives", "multiple", "true_false", "development"]
metadata = generation_runs.metadata

exams = sa.Table(
    "exams",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "generation_id",
        sa.Uuid,
        sa.ForeignKey("app.generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="ready"),
    sa.Column("duration_seconds", sa.Integer, nullable=False),
    sa.Column(
        "published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("invalidated_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("length(btrim(title)) > 0", name="exams_title"),
    sa.CheckConstraint("kind IN ('diagnostic','practice')", name="exams_kind"),
    sa.CheckConstraint("duration_seconds BETWEEN 60 AND 7200", name="exams_duration"),
    sa.CheckConstraint(
        "(status = 'ready' AND invalidated_at IS NULL) OR "
        "(status = 'invalidated' AND invalidated_at IS NOT NULL AND title = 'Evaluación retirada')",
        name="exams_status",
    ),
)

questions = sa.Table(
    "questions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "exam_id", sa.Uuid, sa.ForeignKey("app.exams.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("max_points", sa.Numeric(8, 5), nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("exam_id", "position"),
    sa.CheckConstraint("position BETWEEN 1 AND 20", name="questions_position"),
    sa.CheckConstraint(
        "kind IN ('alternatives','multiple','true_false','development')",
        name="questions_kind",
    ),
    sa.CheckConstraint("length(btrim(prompt)) > 0", name="questions_prompt"),
    sa.CheckConstraint("max_points > 0 AND max_points <= 999.99999", name="questions_points"),
)

question_options = sa.Table(
    "question_options",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "question_id",
        sa.Uuid,
        sa.ForeignKey("app.questions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.UniqueConstraint("question_id", "position"),
    sa.UniqueConstraint("question_id", "id"),
    sa.CheckConstraint("position BETWEEN 1 AND 6", name="question_options_position"),
    sa.CheckConstraint("length(btrim(text)) > 0", name="question_options_text"),
)

question_keys = sa.Table(
    "question_keys",
    metadata,
    sa.Column(
        "question_id",
        sa.Uuid,
        sa.ForeignKey("app.questions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("expected_answer", sa.Text),
    sa.Column("rubric", JSONB),
    sa.Column("explanation", sa.Text, nullable=False),
    sa.CheckConstraint(
        "rubric IS NULL OR jsonb_typeof(rubric) = 'object'", name="question_keys_rubric"
    ),
    sa.CheckConstraint("length(btrim(explanation)) > 0", name="question_keys_explanation"),
)

correct_options = sa.Table(
    "correct_options",
    metadata,
    sa.Column(
        "question_id",
        sa.Uuid,
        sa.ForeignKey("app.question_keys.question_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("option_id", sa.Uuid, primary_key=True),
    sa.ForeignKeyConstraint(
        ["question_id", "option_id"],
        ["app.question_options.question_id", "app.question_options.id"],
        ondelete="CASCADE",
    ),
)


@dataclass(frozen=True)
class ExamQuestion:
    kind: QuestionKind
    prompt: str
    explanation: str
    options: list[str] = field(default_factory=list)
    correct_indices: list[int] = field(default_factory=list)
    expected_answer: str | None = None
    rubric: dict[str, Any] | None = None
    max_points: Decimal = Decimal(1)


def publish_exam(
    conn: Connection,
    course_id: UUID,
    generation_id: UUID,
    *,
    title: str,
    kind: ExamKind,
    duration_seconds: int,
    items: Sequence[ExamQuestion],
) -> UUID:
    """Guarda el examen completo con sus claves en la misma transacción."""
    payload = [
        {
            "kind": item.kind,
            "prompt": item.prompt,
            "explanation": item.explanation,
            "options": item.options,
            "correct_indices": item.correct_indices,
            "expected_answer": item.expected_answer,
            "rubric": item.rubric,
            "max_points": str(item.max_points),
        }
        for item in items
    ]
    statement = sa.select(
        sa.func.app.publish_exam(
            course_id,
            generation_id,
            title,
            kind,
            duration_seconds,
            sa.cast(payload, JSONB),
        )
    )
    return conn.execute(statement).scalar_one()


def list_exams(conn: Connection, course_id: UUID, *, limit: int = 50) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = (
        sa.select(exams)
        .join(generation_runs, generation_runs.c.id == exams.c.generation_id)
        .where(generation_runs.c.course_id == course_id)
        .order_by(exams.c.published_at.desc(), exams.c.id.desc())
        .limit(limit)
    )
    return list(conn.execute(statement).mappings())


def list_exam_questions(conn: Connection, course_id: UUID, exam_id: UUID) -> list[RowMapping]:
    """Devuelve las preguntas y opciones visibles sin incluir datos de corrección."""
    option = sa.func.jsonb_build_object(
        "id",
        question_options.c.id,
        "position",
        question_options.c.position,
        "text",
        question_options.c.text,
    )
    options = (
        sa.select(sa.func.jsonb_agg(aggregate_order_by(option, question_options.c.position)))
        .where(question_options.c.question_id == questions.c.id)
        .scalar_subquery()
    )
    statement = (
        sa.select(questions, sa.func.coalesce(options, sa.cast([], JSONB)).label("options"))
        .join(exams, exams.c.id == questions.c.exam_id)
        .join(generation_runs, generation_runs.c.id == exams.c.generation_id)
        .where(exams.c.id == exam_id, generation_runs.c.course_id == course_id)
        .order_by(questions.c.position)
    )
    return list(conn.execute(statement).mappings())


def install_exams(conn: Connection) -> None:
    for table in (exams, questions, question_options, question_keys, correct_options):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")
    _install_exam_access(conn)
    _install_exam_writes(conn)
    _install_exam_invalidation(conn)


def _install_exam_access(conn: Connection) -> None:
    predicates = {
        "exams": "EXISTS (SELECT FROM app.generation_runs AS g WHERE g.id = exams.generation_id)",
        "questions": (
            "EXISTS (SELECT FROM app.exams AS e WHERE e.id = questions.exam_id AND e.status = 'ready')"
        ),
        "question_options": (
            "EXISTS (SELECT FROM app.questions AS q WHERE q.id = question_options.question_id)"
        ),
    }
    for table, predicate in predicates.items():
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS own_exam_read ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE POLICY own_exam_read ON app.{table} FOR SELECT TO tutoria_api USING ({predicate})"
        )
        conn.exec_driver_sql(f"GRANT SELECT ON app.{table} TO tutoria_api")
    conn.exec_driver_sql(
        "REVOKE ALL ON app.question_keys, app.correct_options FROM PUBLIC, tutoria_api, tutoria_worker"
    )


def _install_exam_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.publish_exam(
            cid uuid, gid uuid, exam_title text, exam_kind text, duration integer, payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            exam_id uuid := gen_random_uuid();
            item record;
            question_id uuid;
            question_kind text;
            option_list jsonb;
            key_list jsonb;
            option_count integer;
            key_count integer;
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
                SELECT FROM app.generation_runs WHERE id = gid AND kind = 'exam' AND status = 'running'
            ) OR NOT EXISTS (
                SELECT FROM app.generation_requests WHERE generation_id = gid
            ) THEN
                RAISE EXCEPTION 'INVALID_EXAM_GENERATION' USING ERRCODE = '22023';
            END IF;
            IF jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'INVALID_EXAM_QUESTIONS' USING ERRCODE = '23514';
            END IF;
            IF jsonb_array_length(payload) NOT BETWEEN 1 AND 20 THEN
                RAISE EXCEPTION 'INVALID_EXAM_QUESTIONS' USING ERRCODE = '23514';
            END IF;

            INSERT INTO app.exams (id, generation_id, title, kind, duration_seconds)
            VALUES (exam_id, gid, exam_title, exam_kind, duration);
            FOR item IN SELECT * FROM jsonb_array_elements(payload) WITH ORDINALITY AS q(value, position)
            LOOP
                question_id := gen_random_uuid();
                question_kind := item.value ->> 'kind';
                option_list := item.value -> 'options';
                key_list := item.value -> 'correct_indices';
                IF jsonb_typeof(option_list) IS DISTINCT FROM 'array'
                    OR jsonb_typeof(key_list) IS DISTINCT FROM 'array' THEN
                    RAISE EXCEPTION 'INVALID_QUESTION_OPTIONS' USING ERRCODE = '23514';
                END IF;
                option_count := jsonb_array_length(option_list);
                key_count := jsonb_array_length(key_list);
                IF question_kind = 'development' THEN
                    IF option_count <> 0 OR key_count <> 0
                        OR nullif(btrim(item.value ->> 'expected_answer'), '') IS NULL
                        OR jsonb_typeof(item.value -> 'rubric') IS DISTINCT FROM 'object' THEN
                        RAISE EXCEPTION 'INVALID_DEVELOPMENT_KEY' USING ERRCODE = '23514';
                    END IF;
                ELSIF option_count NOT BETWEEN 2 AND 6 OR key_count = 0
                    OR (question_kind IN ('alternatives', 'true_false') AND key_count <> 1)
                    OR (question_kind = 'true_false' AND option_count <> 2)
                    OR EXISTS (
                        SELECT FROM jsonb_array_elements(option_list) AS opt(value)
                        WHERE jsonb_typeof(opt.value) <> 'string'
                    )
                    OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(option_list)) <> option_count
                    OR (SELECT count(DISTINCT value::integer) FROM jsonb_array_elements_text(key_list)) <> key_count
                    OR EXISTS (
                        SELECT FROM jsonb_array_elements_text(key_list) AS key(value)
                        WHERE key.value::integer NOT BETWEEN 0 AND option_count - 1
                    ) THEN
                    RAISE EXCEPTION 'INVALID_QUESTION_OPTIONS' USING ERRCODE = '23514';
                END IF;

                INSERT INTO app.questions (id, exam_id, position, kind, prompt, max_points)
                VALUES (
                    question_id, exam_id, item.position, question_kind,
                    item.value ->> 'prompt', (item.value ->> 'max_points')::numeric
                );
                INSERT INTO app.question_keys (question_id, expected_answer, rubric, explanation)
                VALUES (
                    question_id, item.value ->> 'expected_answer',
                    NULLIF(item.value -> 'rubric', 'null'::jsonb), item.value ->> 'explanation'
                );
                WITH inserted AS (
                    INSERT INTO app.question_options (id, question_id, position, text)
                    SELECT gen_random_uuid(), question_id, opt.position, opt.value
                    FROM jsonb_array_elements_text(option_list) WITH ORDINALITY AS opt(value, position)
                    RETURNING id, position
                )
                INSERT INTO app.correct_options (question_id, option_id)
                SELECT question_id, inserted.id
                FROM inserted JOIN jsonb_array_elements_text(key_list) AS key(value)
                  ON inserted.position = key.value::integer + 1;
            END LOOP;

            UPDATE app.generation_runs SET status = 'completed' WHERE id = gid;
            RETURN exam_id;
        END
        $$"""
    )
    signature = "app.publish_exam(uuid, uuid, text, text, integer, jsonb)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")


def _install_exam_invalidation(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.invalidate_generation_exams() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.exams SET status = 'invalidated', title = 'Evaluación retirada',
                invalidated_at = clock_timestamp()
            WHERE generation_id = NEW.id AND status = 'ready';
            DELETE FROM app.question_keys AS k USING app.questions AS q, app.exams AS e
            WHERE k.question_id = q.id AND q.exam_id = e.id AND e.generation_id = NEW.id;
            DELETE FROM app.question_options AS o USING app.questions AS q, app.exams AS e
            WHERE o.question_id = q.id AND q.exam_id = e.id AND e.generation_id = NEW.id;
            UPDATE app.questions AS q SET prompt = 'Pregunta retirada por eliminación de fuente'
            FROM app.exams AS e WHERE q.exam_id = e.id AND e.generation_id = NEW.id;
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "DROP TRIGGER IF EXISTS invalidate_generation_exams ON app.generation_runs"
    )
    conn.exec_driver_sql(
        """CREATE TRIGGER invalidate_generation_exams AFTER UPDATE OF status ON app.generation_runs
        FOR EACH ROW WHEN (NEW.status = 'invalidated' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.invalidate_generation_exams()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.invalidate_generation_exams() FROM PUBLIC")
