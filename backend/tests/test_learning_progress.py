from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
import test_exams
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_exam_attempts import ALTERNATIVES, _exam
from test_exams import DEVELOPMENT, _generation, _publish, _role

from db.exam_attempts import (
    ExamAnswer,
    list_attempt_answers,
    review_development_answer,
    start_exam_attempt,
    submit_exam_attempt,
)
from db.generations import generation_runs
from db.learning_catalog import create_concept
from db.learning_progress import list_concept_mastery, list_mastery_evidence, mastery_events

progress_context = test_exams.exam_context


def _submit_choice(conn, course, exam, question, *, correct):
    attempt = start_exam_attempt(conn, course, exam)
    option = question["options"][1 if correct else 0]["id"]
    submit_exam_attempt(conn, course, attempt, [ExamAnswer(question["id"], [option])])
    return attempt


def test_progress_reuses_beta_thresholds_and_first_graded_attempt(postgres, progress_context):
    owner, peer, course, other = progress_context
    concept = create_concept(postgres, course, label="Fracciones")
    unknown = create_concept(postgres, course, label="Geometría")
    create_concept(postgres, other, label="Fracciones")
    item = replace(ALTERNATIVES, concept_id=concept)
    first, second = list_concept_mastery(postgres, course)
    assert (first["concept_id"], first["score"], first["level"]) == (
        concept,
        Decimal("0.5"),
        "unknown",
    )
    assert (second["concept_id"], second["evidence_count"]) == (unknown, 0)

    for count, score, level in (
        (1, Decimal(2) / Decimal(3), "developing"),
        (2, Decimal("0.75"), "developing"),
        (3, Decimal("0.8"), "mastered"),
    ):
        _, exam, (question,) = _exam(postgres, course, [item])
        assert question["concept_id"] == concept
        attempt = _submit_choice(postgres, course, exam, question, correct=True)
        if count == 1:
            assert submit_exam_attempt(postgres, course, attempt, []) == attempt
            _submit_choice(postgres, course, exam, question, correct=False)
        row = list_concept_mastery(postgres, course)[0]
        assert row["evidence_count"] == count and row["level"] == level
        assert abs(row["score"] - score) < Decimal("0.000001")
    assert [event["value"] for event in list_mastery_evidence(postgres, course, concept)] == [
        1,
        1,
        1,
    ]

    _role(postgres, peer)
    assert list_concept_mastery(postgres, course)[0]["level"] == "unknown"
    assert list_mastery_evidence(postgres, course, concept) == []
    assert list_concept_mastery(postgres, other) == []
    _role(postgres, owner)
    assert list_mastery_evidence(postgres, other, concept) == []
    with pytest.raises(ProgrammingError, match="permission denied"), postgres.begin_nested():
        postgres.execute(
            insert(mastery_events).values(
                grade_id=uuid4(),
                course_id=course,
                user_id=owner,
                concept_id=concept,
                value=1,
                weight=1,
            )
        )


def test_manual_review_closes_attempt_and_invalidation_removes_evidence(postgres, progress_context):
    owner, _, course, _ = progress_context
    concept = create_concept(postgres, course, label="Funciones")
    generation, exam, (choice, written) = _exam(
        postgres,
        course,
        [replace(ALTERNATIVES, concept_id=concept), replace(DEVELOPMENT, concept_id=concept)],
    )
    attempt = start_exam_attempt(postgres, course, exam)
    submit_exam_attempt(
        postgres,
        course,
        attempt,
        [
            ExamAnswer(choice["id"], [choice["options"][0]["id"]]),
            ExamAnswer(written["id"], text_answer="Explicación"),
        ],
    )
    assert list_mastery_evidence(postgres, course, concept) == []
    _, answer = list_attempt_answers(postgres, course, attempt)
    review_development_answer(
        postgres, course, attempt, answer["id"], points=Decimal(1), feedback="Bien"
    )
    (event,) = list_mastery_evidence(postgres, course, concept)
    assert (event["value"], event["weight"], event["algorithm_version"]) == (0, 1, "beta_v1")
    (row,) = list_concept_mastery(postgres, course)
    assert (row["alpha"], row["beta"], row["level"]) == (1, 2, "reinforce")

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(generation_runs)
        .where(generation_runs.c.id == generation)
        .values(status="invalidated")
    )
    _role(postgres, owner)
    assert list_mastery_evidence(postgres, course, concept) == []
    assert list_concept_mastery(postgres, course)[0]["level"] == "unknown"


def test_exam_concept_is_optional_and_must_belong_to_course(postgres, progress_context):
    _, _, course, other = progress_context
    concept = create_concept(postgres, course, label="Concepto local")
    foreign = create_concept(postgres, other, label="Otro concepto")
    generation = _generation(postgres, course)
    for invalid in (foreign, uuid4()):
        with (
            pytest.raises(IntegrityError, match="INVALID_QUESTION_CONCEPT"),
            postgres.begin_nested(),
        ):
            _publish(postgres, course, generation, [replace(ALTERNATIVES, concept_id=invalid)])
    _, exam, (question,) = _exam(postgres, course, [ALTERNATIVES])
    assert question["concept_id"] is None
    _submit_choice(postgres, course, exam, question, correct=True)
    (row,) = list_concept_mastery(postgres, course)
    assert (row["concept_id"], row["evidence_count"]) == (concept, 0)
