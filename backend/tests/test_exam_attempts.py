from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import test_exams
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_exams import DEVELOPMENT, MULTIPLE, _generation, _publish, _role

from db.exam_attempts import (
    ExamAnswer,
    answers,
    exam_attempts,
    get_exam_attempt,
    list_attempt_answers,
    list_exam_attempts,
    start_exam_attempt,
    submit_exam_attempt,
)
from db.exams import list_exam_questions
from db.generations import generation_runs

attempt_context = test_exams.exam_context

ALTERNATIVES = replace(
    MULTIPLE, kind="alternatives", correct_indices=[1], max_points=Decimal("1.5")
)


def _exam(conn, course, items):
    generation = _generation(conn, course)
    exam = _publish(conn, course, generation, items)
    return generation, exam, list_exam_questions(conn, course, exam)


def _options(question):
    return [option["id"] for option in question["options"]]


def test_choice_grading_requires_exact_selection_and_preserves_submission(
    postgres, attempt_context
):
    _, _, course, _ = attempt_context
    _, exam, questions = _exam(postgres, course, [MULTIPLE, MULTIPLE, MULTIPLE, ALTERNATIVES])
    attempt = start_exam_attempt(postgres, course, exam)
    assert start_exam_attempt(postgres, course, exam) == attempt
    opened = get_exam_attempt(postgres, course, attempt)
    assert opened["deadline_at"] - opened["started_at"] == timedelta(seconds=600)
    first, second, third, _ = questions
    a, _, c = _options(first)
    partial, _, _ = _options(second)
    assert (
        submit_exam_attempt(
            postgres,
            course,
            attempt,
            [
                ExamAnswer(first["id"], [c, a]),
                ExamAnswer(second["id"], [partial]),
                ExamAnswer(third["id"], _options(third)),
            ],
        )
        == attempt
    )
    graded = get_exam_attempt(postgres, course, attempt)
    assert (graded["status"], graded["score_percent"]) == ("graded", Decimal("27.77778"))
    responses = list_attempt_answers(postgres, course, attempt)
    assert [row["points"] for row in responses] == [Decimal("2.5"), 0, 0, 0]
    assert {str(value) for value in responses[0]["selected_option_ids"]} == {str(a), str(c)}
    assert responses[-1]["selected_option_ids"] == []
    assert submit_exam_attempt(postgres, course, attempt, []) == attempt
    assert list_attempt_answers(postgres, course, attempt) == responses
    next_attempt = start_exam_attempt(postgres, course, exam)
    assert [row["id"] for row in list_exam_attempts(postgres, course, exam)] == [
        next_attempt,
        attempt,
    ]
    assert get_exam_attempt(postgres, course, next_attempt)["attempt_no"] == 2


def test_written_answer_keeps_its_text_and_leaves_final_score_pending(postgres, attempt_context):
    _, _, course, _ = attempt_context
    _, exam, (choice, written, _) = _exam(
        postgres, course, [ALTERNATIVES, DEVELOPMENT, DEVELOPMENT]
    )
    attempt = start_exam_attempt(postgres, course, exam)
    _, correct, _ = _options(choice)
    text = "  Explicación del usuario.\n"
    assert (
        submit_exam_attempt(
            postgres,
            course,
            attempt,
            [ExamAnswer(choice["id"], [correct]), ExamAnswer(written["id"], text_answer=text)],
        )
        == attempt
    )
    result = get_exam_attempt(postgres, course, attempt)
    assert (result["status"], result["score_percent"]) == ("needs_review", None)
    graded, pending, omitted = list_attempt_answers(postgres, course, attempt)
    assert (graded["grade_status"], graded["points"]) == ("accepted", Decimal("1.5"))
    assert (pending["grade_status"], pending["points"], pending["text_answer"]) == (
        "needs_review",
        None,
        text,
    )
    assert (omitted["grade_status"], omitted["points"], omitted["text_answer"]) == (
        "accepted",
        0,
        None,
    )


def test_invalid_answers_roll_back_the_submission(postgres, attempt_context):
    _, _, course, _ = attempt_context
    _, exam, (first, second) = _exam(postgres, course, [MULTIPLE, ALTERNATIVES])
    attempt = start_exam_attempt(postgres, course, exam)
    a, b, _ = _options(second)
    for payload, error in (
        ([ExamAnswer(uuid4())], "INVALID_EXAM_ANSWERS"),
        ([ExamAnswer(first["id"]), ExamAnswer(first["id"])], "INVALID_EXAM_ANSWERS"),
        ([ExamAnswer(first["id"], [a])], "INVALID_ANSWER_OPTIONS"),
        ([ExamAnswer(second["id"], [a, a])], "INVALID_ANSWER_OPTIONS"),
        ([ExamAnswer(second["id"], [a, b])], "INVALID_ANSWER_OPTIONS"),
        ([ExamAnswer(second["id"], text_answer="texto")], "INVALID_ANSWER_OPTIONS"),
    ):
        with pytest.raises(IntegrityError, match=error), postgres.begin_nested():
            submit_exam_attempt(postgres, course, attempt, payload)
        assert get_exam_attempt(postgres, course, attempt)["status"] == "in_progress"
        assert list_attempt_answers(postgres, course, attempt) == []
    assert submit_exam_attempt(postgres, course, attempt, []) == attempt


def test_expiration_and_owner_access(postgres, attempt_context):
    owner, peer, course, other_course = attempt_context
    _, exam, _ = _exam(postgres, course, [MULTIPLE])
    attempt = start_exam_attempt(postgres, course, exam)
    assert get_exam_attempt(postgres, other_course, attempt) is None
    _role(postgres, peer)
    assert get_exam_attempt(postgres, course, attempt) is None
    assert list_exam_attempts(postgres, course, exam) == []
    assert list_attempt_answers(postgres, course, attempt) == []
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        start_exam_attempt(postgres, course, exam)
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        submit_exam_attempt(postgres, course, attempt, [])
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(exam_attempts)
        .where(exam_attempts.c.id == attempt)
        .values(
            started_at=func.now() - timedelta(minutes=20),
            deadline_at=func.now() - timedelta(minutes=10),
        )
    )
    _role(postgres, owner)
    assert submit_exam_attempt(postgres, course, attempt, []) is None
    assert get_exam_attempt(postgres, course, attempt)["status"] == "expired"
    assert list_attempt_answers(postgres, course, attempt) == []
    assert start_exam_attempt(postgres, course, exam) != attempt


def test_exam_invalidation_removes_answers_and_score(postgres, attempt_context):
    _, _, course, _ = attempt_context
    generation, exam, (question,) = _exam(postgres, course, [ALTERNATIVES])
    attempt = start_exam_attempt(postgres, course, exam)
    _, correct, _ = _options(question)
    assert (
        submit_exam_attempt(postgres, course, attempt, [ExamAnswer(question["id"], [correct])])
        == attempt
    )

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(generation_runs)
        .where(generation_runs.c.id == generation)
        .values(status="invalidated")
    )
    result = get_exam_attempt(postgres, course, attempt)
    assert (result["status"], result["score_percent"]) == ("invalidated", None)
    assert not postgres.execute(select(answers.c.id).where(answers.c.attempt_id == attempt)).all()
