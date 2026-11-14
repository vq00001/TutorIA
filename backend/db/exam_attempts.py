"""Intentos y corrección determinista, adaptados del ZIP de TutorIA."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.schema import AddConstraint, DropConstraint

from .exams import exams, questions
from .generations import generation_runs

metadata = exams.metadata
_question_exam_key = sa.Index(
    "questions_exam_id_key",
    questions.c.exam_id,
    questions.c.id,
    unique=True,
)

exam_attempts = sa.Table(
    "exam_attempts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "exam_id", sa.Uuid, sa.ForeignKey("app.exams.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("attempt_no", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="in_progress"),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("submitted_at", sa.DateTime(timezone=True)),
    sa.Column("graded_at", sa.DateTime(timezone=True)),
    sa.Column("score_percent", sa.Numeric(8, 5)),
    sa.UniqueConstraint("exam_id", "attempt_no"),
    sa.UniqueConstraint("exam_id", "id"),
    sa.CheckConstraint("attempt_no > 0 AND deadline_at > started_at", name="attempts_bounds"),
    sa.CheckConstraint(
        "status IN ('in_progress', 'expired', 'graded', 'needs_review', 'invalidated')",
        name="attempts_status",
    ),
    sa.CheckConstraint(
        "(status = 'graded' AND score_percent IS NOT NULL AND score_percent BETWEEN 0 AND 100) "
        "OR (status <> 'graded' AND score_percent IS NULL)",
        name="attempts_score",
    ),
)
sa.Index(
    "exam_attempts_one_open",
    exam_attempts.c.exam_id,
    unique=True,
    postgresql_where=exam_attempts.c.status == "in_progress",
)

answers = sa.Table(
    "answers",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("exam_id", sa.Uuid, nullable=False),
    sa.Column("attempt_id", sa.Uuid, nullable=False),
    sa.Column("question_id", sa.Uuid, nullable=False),
    sa.Column("text_answer", sa.Text),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["exam_id", "attempt_id"],
        ["app.exam_attempts.exam_id", "app.exam_attempts.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["exam_id", "question_id"],
        ["app.questions.exam_id", "app.questions.id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("attempt_id", "question_id"),
    sa.UniqueConstraint("question_id", "id"),
    sa.CheckConstraint("char_length(text_answer) <= 20000", name="answers_text_length"),
)

answer_selections = sa.Table(
    "answer_selections",
    metadata,
    sa.Column("answer_id", sa.Uuid, primary_key=True),
    sa.Column("option_id", sa.Uuid, primary_key=True),
    sa.Column("question_id", sa.Uuid, nullable=False),
    sa.ForeignKeyConstraint(
        ["question_id", "answer_id"],
        ["app.answers.question_id", "app.answers.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["question_id", "option_id"],
        ["app.question_options.question_id", "app.question_options.id"],
        ondelete="CASCADE",
    ),
)

_grade_status_points = sa.CheckConstraint(
    "(status = 'accepted' AND points IS NOT NULL AND points BETWEEN 0 AND 999.99999 "
    "AND method IS NOT NULL AND ("
    "(method = 'deterministic' AND feedback IS NULL) OR "
    "(method = 'manual' AND feedback IS NOT NULL AND char_length(feedback) <= 10000 "
    "AND feedback ~ '[^[:space:]]'))) OR "
    "(status = 'needs_review' AND points IS NULL AND method IS NULL AND feedback IS NULL)",
    name="grades_status_points",
)

grades = sa.Table(
    "grades",
    metadata,
    sa.Column(
        "answer_id", sa.Uuid, sa.ForeignKey("app.answers.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("points", sa.Numeric(8, 5)),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("method", sa.Text),
    sa.Column("feedback", sa.Text),
    sa.Column("grader_version", sa.Text, nullable=False, server_default="v1"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    _grade_status_points,
)


@dataclass(frozen=True)
class ExamAnswer:
    question_id: UUID
    selected_option_ids: list[UUID] = field(default_factory=list)
    text_answer: str | None = None


def start_exam_attempt(conn: Connection, course_id: UUID, exam_id: UUID) -> UUID:
    """Inicia un intento o recupera el intento abierto que todavía está vigente."""
    return conn.execute(sa.select(sa.func.app.start_exam_attempt(course_id, exam_id))).scalar_one()


def submit_exam_attempt(
    conn: Connection,
    course_id: UUID,
    attempt_id: UUID,
    items: Sequence[ExamAnswer],
) -> UUID | None:
    """Guarda y corrige la entrega. Devuelve None si el intento venció."""
    payload = [
        {
            "question_id": str(item.question_id),
            "selected_option_ids": [str(option_id) for option_id in item.selected_option_ids],
            "text_answer": item.text_answer,
        }
        for item in items
    ]
    statement = sa.select(
        sa.func.app.submit_exam_attempt(
            course_id,
            attempt_id,
            sa.cast(payload, JSONB),
        )
    )
    return conn.scalar(statement)


def review_development_answer(
    conn: Connection,
    course_id: UUID,
    attempt_id: UUID,
    answer_id: UUID,
    *,
    points: Decimal,
    feedback: str,
) -> UUID:
    """Registra la revisión manual y devuelve el ID de la respuesta revisada."""
    return conn.execute(
        sa.select(
            sa.func.app.review_development_answer(
                course_id, attempt_id, answer_id, sa.cast(points, sa.Numeric), feedback
            )
        )
    ).scalar_one()


def _course_attempts(course_id: UUID):
    return (
        sa.select(exam_attempts)
        .join(exams, exams.c.id == exam_attempts.c.exam_id)
        .join(generation_runs, generation_runs.c.id == exams.c.generation_id)
        .where(generation_runs.c.course_id == course_id)
    )


def list_exam_attempts(
    conn: Connection,
    course_id: UUID,
    exam_id: UUID,
    *,
    limit: int = 50,
) -> list[RowMapping]:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = (
        _course_attempts(course_id)
        .where(exam_attempts.c.exam_id == exam_id)
        .order_by(exam_attempts.c.attempt_no.desc())
        .limit(limit)
    )
    return list(conn.execute(statement).mappings())


def get_exam_attempt(conn: Connection, course_id: UUID, attempt_id: UUID) -> RowMapping | None:
    statement = _course_attempts(course_id).where(exam_attempts.c.id == attempt_id)
    return conn.execute(statement).mappings().one_or_none()


def list_attempt_answers(
    conn: Connection,
    course_id: UUID,
    attempt_id: UUID,
) -> list[RowMapping]:
    selected = (
        sa.select(
            sa.func.jsonb_agg(
                aggregate_order_by(
                    answer_selections.c.option_id,
                    answer_selections.c.option_id,
                )
            )
        )
        .where(answer_selections.c.answer_id == answers.c.id)
        .scalar_subquery()
    )
    statement = (
        sa.select(
            answers,
            grades.c.points,
            grades.c.feedback,
            grades.c.status.label("grade_status"),
            grades.c.method.label("grade_method"),
            sa.func.coalesce(selected, sa.cast([], JSONB)).label("selected_option_ids"),
        )
        .join(grades, grades.c.answer_id == answers.c.id)
        .join(questions, questions.c.id == answers.c.question_id)
        .join(exams, exams.c.id == answers.c.exam_id)
        .join(generation_runs, generation_runs.c.id == exams.c.generation_id)
        .where(answers.c.attempt_id == attempt_id, generation_runs.c.course_id == course_id)
        .order_by(questions.c.position)
    )
    return list(conn.execute(statement).mappings())


def install_exam_attempts(conn: Connection) -> None:
    _question_exam_key.create(conn, checkfirst=True)
    for table in (exam_attempts, answers, answer_selections, grades):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE app.{table.name} FORCE ROW LEVEL SECURITY")
    # Permite volver a ejecutar db-init sobre el esquema anterior de intentos.
    if not any(
        column["name"] == "feedback"
        for column in sa.inspect(conn).get_columns("grades", schema="app")
    ):
        conn.exec_driver_sql("ALTER TABLE app.grades ADD COLUMN feedback text")
        conn.execute(DropConstraint(_grade_status_points, isolate_from_table=False))
        conn.execute(AddConstraint(_grade_status_points, isolate_from_table=False))
    _install_attempt_access(conn)
    _install_attempt_writes(conn)
    _install_development_review(conn)
    _install_attempt_invalidation(conn)


def _install_attempt_access(conn: Connection) -> None:
    predicates = {
        "exam_attempts": "EXISTS (SELECT FROM app.exams AS e WHERE e.id = exam_attempts.exam_id)",
        "answers": "EXISTS (SELECT FROM app.exam_attempts AS a WHERE a.id = answers.attempt_id)",
        "answer_selections": "EXISTS (SELECT FROM app.answers AS a WHERE a.id = answer_selections.answer_id)",
        "grades": "EXISTS (SELECT FROM app.answers AS a WHERE a.id = grades.answer_id)",
    }
    for table, predicate in predicates.items():
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS own_attempt_read ON app.{table}")
        conn.exec_driver_sql(
            f"CREATE POLICY own_attempt_read ON app.{table} FOR SELECT TO tutoria_api USING ({predicate})"
        )
        conn.exec_driver_sql(f"GRANT SELECT ON app.{table} TO tutoria_api")


def _install_attempt_writes(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.lock_attempt_exam(cid uuid, eid uuid)
        RETURNS boolean
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE actor uuid := app.course_actor();
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR SHARE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM app.exams AS e JOIN app.generation_runs AS g ON g.id = e.generation_id
            WHERE e.id = eid AND g.course_id = cid AND g.user_id = actor
              AND e.status = 'ready' AND g.status = 'completed' FOR UPDATE OF e;
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.lock_attempt_exam(uuid, uuid) FROM PUBLIC")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.start_exam_attempt(cid uuid, eid uuid)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            attempt uuid;
            opened_at timestamptz;
            duration integer;
            next_attempt integer;
        BEGIN
            IF NOT app.lock_attempt_exam(cid, eid) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            opened_at := clock_timestamp();
            UPDATE app.exam_attempts SET status = 'expired'
            WHERE exam_id = eid AND status = 'in_progress' AND deadline_at <= opened_at;
            SELECT id INTO attempt FROM app.exam_attempts WHERE exam_id = eid AND status = 'in_progress';
            IF FOUND THEN
                RETURN attempt;
            END IF;

            SELECT duration_seconds INTO duration FROM app.exams WHERE id = eid;
            SELECT coalesce(max(attempt_no), 0) + 1 INTO next_attempt
            FROM app.exam_attempts WHERE exam_id = eid;
            attempt := gen_random_uuid();
            INSERT INTO app.exam_attempts (id, exam_id, attempt_no, started_at, deadline_at)
            VALUES (attempt, eid, next_attempt, opened_at, opened_at + make_interval(secs => duration));
            RETURN attempt;
        END
        $$"""
    )
    _install_attempt_submission(conn)
    signatures = "app.start_exam_attempt(uuid, uuid), app.submit_exam_attempt(uuid, uuid, jsonb)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")


def _install_attempt_submission(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.submit_exam_attempt(cid uuid, aid uuid, payload jsonb)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            eid uuid;
            attempt app.exam_attempts;
            item record;
            answer_id uuid;
            option_ids jsonb;
            answer_text text;
            submitted timestamptz;
        BEGIN
            SELECT a.exam_id INTO eid FROM app.exam_attempts AS a
            JOIN app.exams AS e ON e.id = a.exam_id
            JOIN app.generation_runs AS g ON g.id = e.generation_id
            WHERE a.id = aid AND g.course_id = cid AND g.user_id = app.course_actor();
            IF NOT FOUND OR NOT app.lock_attempt_exam(cid, eid) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            SELECT * INTO attempt FROM app.exam_attempts WHERE id = aid AND exam_id = eid FOR UPDATE;
            IF NOT FOUND OR attempt.status = 'invalidated' THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF attempt.status IN ('graded', 'needs_review') THEN
                RETURN aid;
            END IF;
            submitted := clock_timestamp();
            IF attempt.status = 'expired' OR attempt.deadline_at <= submitted THEN
                UPDATE app.exam_attempts SET status = 'expired' WHERE id = aid;
                RETURN NULL;
            END IF;

            IF jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'INVALID_EXAM_ANSWERS' USING ERRCODE = '23514';
            END IF;
            IF jsonb_array_length(payload) > 20 OR (
                SELECT count(DISTINCT (input.value ->> 'question_id')::uuid)
                FROM jsonb_array_elements(payload) AS input(value)
            ) <> jsonb_array_length(payload) OR EXISTS (
                SELECT FROM jsonb_array_elements(payload) AS input(value)
                WHERE NOT EXISTS (
                    SELECT FROM app.questions AS q
                    WHERE q.exam_id = eid AND q.id = (input.value ->> 'question_id')::uuid
                )
            ) THEN
                RAISE EXCEPTION 'INVALID_EXAM_ANSWERS' USING ERRCODE = '23514';
            END IF;

            -- Incluye las preguntas omitidas, como en el cierre original del ZIP.
            FOR item IN
                SELECT q.id, q.kind, entry.value FROM app.questions AS q
                LEFT JOIN jsonb_array_elements(payload) AS entry(value)
                  ON q.id = (entry.value ->> 'question_id')::uuid
                WHERE q.exam_id = eid
            LOOP
                option_ids := coalesce(item.value -> 'selected_option_ids', '[]'::jsonb);
                answer_text := item.value ->> 'text_answer';
                IF jsonb_typeof(option_ids) IS DISTINCT FROM 'array'
                    OR (item.kind <> 'development' AND answer_text IS NOT NULL) THEN
                    RAISE EXCEPTION 'INVALID_ANSWER_OPTIONS' USING ERRCODE = '23514';
                END IF;
                IF jsonb_array_length(option_ids) > 6
                    OR (item.kind = 'development' AND jsonb_array_length(option_ids) <> 0)
                    OR (item.kind IN ('alternatives', 'true_false') AND jsonb_array_length(option_ids) > 1)
                    OR (
                        SELECT count(DISTINCT value::uuid) FROM jsonb_array_elements_text(option_ids)
                    ) <> jsonb_array_length(option_ids)
                    OR EXISTS (
                        SELECT FROM jsonb_array_elements_text(option_ids) AS selected(value)
                        WHERE NOT EXISTS (
                            SELECT FROM app.question_options AS o
                            WHERE o.question_id = item.id AND o.id = selected.value::uuid
                        )
                    ) THEN
                    RAISE EXCEPTION 'INVALID_ANSWER_OPTIONS' USING ERRCODE = '23514';
                END IF;

                answer_id := gen_random_uuid();
                INSERT INTO app.answers (id, exam_id, attempt_id, question_id, text_answer)
                VALUES (answer_id, eid, aid, item.id, answer_text);
                INSERT INTO app.answer_selections (answer_id, option_id, question_id)
                SELECT answer_id, selected.value::uuid, item.id
                FROM jsonb_array_elements_text(option_ids) AS selected(value);
            END LOOP;

            -- La selección debe coincidir exactamente con el conjunto de claves.
            WITH results AS (
                SELECT a.id, q.max_points,
                    q.kind = 'development' AND nullif(btrim(a.text_answer), '') IS NOT NULL AS pending,
                    q.kind <> 'development' AND NOT EXISTS (
                        SELECT s.option_id FROM app.answer_selections AS s WHERE s.answer_id = a.id
                        EXCEPT
                        SELECT k.option_id FROM app.correct_options AS k WHERE k.question_id = q.id
                    ) AND NOT EXISTS (
                        SELECT k.option_id FROM app.correct_options AS k WHERE k.question_id = q.id
                        EXCEPT
                        SELECT s.option_id FROM app.answer_selections AS s WHERE s.answer_id = a.id
                    ) AS exact_match
                FROM app.answers AS a JOIN app.questions AS q ON q.id = a.question_id
                WHERE a.attempt_id = aid
            )
            INSERT INTO app.grades (answer_id, points, status, method)
            SELECT id, CASE WHEN pending THEN NULL WHEN exact_match THEN max_points ELSE 0 END,
                CASE WHEN pending THEN 'needs_review' ELSE 'accepted' END,
                CASE WHEN pending THEN NULL ELSE 'deterministic' END
            FROM results;

            UPDATE app.exam_attempts SET submitted_at = submitted WHERE id = aid;
            PERFORM app.refresh_exam_attempt_grade(aid);
            RETURN aid;
        END
        $$"""
    )


def _install_development_review(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.refresh_exam_attempt_grade(aid uuid) RETURNS void
        LANGUAGE sql SET search_path = pg_catalog, pg_temp
        AS $$
            UPDATE app.exam_attempts
            SET status = CASE WHEN result.pending THEN 'needs_review' ELSE 'graded' END,
                score_percent = CASE WHEN result.pending THEN NULL ELSE result.percent END,
                graded_at = CASE WHEN result.pending THEN NULL ELSE clock_timestamp() END
            FROM (
                SELECT bool_or(g.status = 'needs_review') AS pending,
                    100 * sum(g.points) / sum(q.max_points) AS percent
                FROM app.answers AS a
                JOIN app.grades AS g ON g.answer_id = a.id
                JOIN app.questions AS q ON q.id = a.question_id
                WHERE a.attempt_id = aid
            ) AS result
            WHERE id = aid
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.refresh_exam_attempt_grade(uuid) FROM PUBLIC")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.review_development_answer(
            cid uuid, aid uuid, target_answer uuid, awarded numeric, review_feedback text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            eid uuid;
            attempt_status text;
            grade record;
        BEGIN
            SELECT a.exam_id INTO eid FROM app.exam_attempts AS a
            JOIN app.exams AS e ON e.id = a.exam_id
            JOIN app.generation_runs AS g ON g.id = e.generation_id
            WHERE a.id = aid AND g.course_id = cid AND g.user_id = app.course_actor();
            IF NOT FOUND OR NOT app.lock_attempt_exam(cid, eid) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            SELECT status INTO attempt_status FROM app.exam_attempts WHERE id = aid FOR UPDATE;
            IF attempt_status NOT IN ('needs_review', 'graded') THEN
                RAISE EXCEPTION 'ATTEMPT_NOT_SUBMITTED' USING ERRCODE = '23514';
            END IF;
            SELECT g.status, g.method, g.points, g.feedback, q.max_points INTO grade
            FROM app.answers AS a
            JOIN app.questions AS q ON q.id = a.question_id
            JOIN app.grades AS g ON g.answer_id = a.id
            WHERE a.id = target_answer AND a.attempt_id = aid AND q.kind = 'development';
            IF NOT FOUND OR awarded IS NULL OR NOT (awarded BETWEEN 0 AND grade.max_points)
                OR awarded <> trunc(awarded, 5) OR review_feedback IS NULL
                OR char_length(review_feedback) > 10000 OR review_feedback !~ '[^[:space:]]' THEN
                RAISE EXCEPTION 'INVALID_DEVELOPMENT_REVIEW' USING ERRCODE = '23514';
            END IF;

            IF grade.method = 'manual' AND grade.points = awarded AND grade.feedback = review_feedback THEN
                RETURN target_answer;
            END IF;
            IF attempt_status <> 'needs_review' OR grade.status <> 'needs_review' THEN
                RAISE EXCEPTION 'ANSWER_NOT_PENDING_REVIEW' USING ERRCODE = '23514';
            END IF;

            UPDATE app.grades
            SET points = awarded, status = 'accepted', method = 'manual',
                feedback = review_feedback, grader_version = 'manual_self'
            WHERE answer_id = target_answer;
            PERFORM app.refresh_exam_attempt_grade(aid);
            RETURN target_answer;
        END
        $$"""
    )
    signature = "app.review_development_answer(uuid, uuid, uuid, numeric, text)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signature} TO tutoria_api")


def _install_attempt_invalidation(conn: Connection) -> None:
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.invalidate_exam_attempts() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE app.exam_attempts SET status = 'invalidated', score_percent = NULL, graded_at = NULL
            WHERE exam_id = NEW.id AND status <> 'invalidated';
            DELETE FROM app.answers WHERE exam_id = NEW.id;
            RETURN NULL;
        END
        $$"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS invalidate_exam_attempts ON app.exams")
    conn.exec_driver_sql(
        """CREATE TRIGGER invalidate_exam_attempts AFTER UPDATE OF status ON app.exams
        FOR EACH ROW WHEN (NEW.status = 'invalidated' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app.invalidate_exam_attempts()"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.invalidate_exam_attempts() FROM PUBLIC")
