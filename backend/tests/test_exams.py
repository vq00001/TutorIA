from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.courses import create_course, memberships
from db.documents import delete_document, register_upload
from db.exams import (
    ExamQuestion,
    correct_options,
    list_exam_questions,
    list_exams,
    publish_exam,
    question_keys,
    question_options,
)
from db.generations import generation_runs, record_generation_request, start_generation
from db.processing import (
    ChildChunk,
    ParentChunk,
    publish_processing,
    register_embedding_space,
    start_processing,
)
from db.users import users
from db.vector_search import search_course_chunks

REQUEST = b'{"model":"local","messages":[{"role":"user","content":"Question"}]}'
MULTIPLE = ExamQuestion(
    "multiple",
    "Selecciona dos elementos.",
    "A y C son correctas.",
    options=["A", "B", "C"],
    correct_indices=[0, 2],
    max_points=Decimal("2.5"),
)
DEVELOPMENT = ExamQuestion(
    "development",
    "Explica el procedimiento.",
    "Debe justificar los pasos.",
    expected_answer="Justificación esperada",
    rubric={"criteria": [{"name": "Pasos", "maxPoints": 1}]},
)


def _role(conn, actor, *, worker=False):
    conn.exec_driver_sql(
        "SET LOCAL ROLE tutoria_worker" if worker else "SET LOCAL ROLE tutoria_api"
    )
    set_user_context(conn, actor)


@pytest.fixture
def exam_context(postgres):
    owner, peer = uuid4(), uuid4()
    postgres.execute(
        insert(users),
        [
            {
                "id": uid,
                "nickname": f"u_{uid.hex}",
                "email": f"{uid.hex}@example.test",
                "display_name": "Synthetic",
                "password_hash": "test-only",
                "status": "active",
            }
            for uid in (owner, peer)
        ],
    )
    _role(postgres, owner)
    course = create_course(postgres, name="Clase")
    other_course = create_course(postgres, name="Otra clase")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="student"))
    _role(postgres, owner)
    return owner, peer, course, other_course


def _generation(conn, course, *, kind="exam", sources=(), record=True):
    generation = start_generation(
        conn,
        course,
        kind=kind,
        provider="ollama",
        model_name="local",
        model_revision="sha",
        prompt_version="v1",
    )
    if record:
        record_generation_request(conn, generation, REQUEST, sources)
    return generation


def _publish(conn, course, generation, items):
    return publish_exam(
        conn,
        course,
        generation,
        title="Práctica",
        kind="practice",
        duration_seconds=600,
        items=items,
    )


def test_exam_keeps_order_and_correct_keys_separate_from_student_reads(postgres, exam_context):
    owner, _, course, other_course = exam_context
    generation = _generation(postgres, course)
    exam = _publish(postgres, course, generation, [MULTIPLE, DEVELOPMENT])
    (row,) = list_exams(postgres, course)
    assert (row["id"], row["generation_id"], row["duration_seconds"]) == (exam, generation, 600)
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == generation,
            )
        )
        == "completed"
    )
    first, second = list_exam_questions(postgres, course, exam)
    assert (first["position"], first["max_points"], second["position"]) == (1, Decimal("2.5"), 2)
    assert [option["text"] for option in first["options"]] == ["A", "B", "C"]
    assert all(set(option) == {"id", "position", "text"} for option in first["options"])
    assert second["options"] == []
    assert "expected_answer" not in second and "explanation" not in first
    assert list_exam_questions(postgres, other_course, exam) == []

    for worker in (False, True):
        _role(postgres, owner, worker=worker)
        for table in (question_keys, correct_options):
            with pytest.raises(ProgrammingError), postgres.begin_nested():
                postgres.execute(select(table))

    postgres.exec_driver_sql("RESET ROLE")
    selected = (
        postgres.execute(
            select(question_options.c.text)
            .join(correct_options, correct_options.c.option_id == question_options.c.id)
            .where(correct_options.c.question_id == first["id"])
        )
        .scalars()
        .all()
    )
    assert set(selected) == {"A", "C"}
    development = (
        postgres.execute(
            select(question_keys).where(
                question_keys.c.question_id == second["id"],
            )
        )
        .mappings()
        .one()
    )
    assert development["expected_answer"] == DEVELOPMENT.expected_answer
    assert development["rubric"] == DEVELOPMENT.rubric


def test_invalid_keys_roll_back_the_entire_exam(postgres, exam_context):
    _, _, course, _ = exam_context
    generation = _generation(postgres, course)
    for invalid, error in (
        (replace(MULTIPLE, correct_indices=[3]), "INVALID_QUESTION_OPTIONS"),
        (replace(MULTIPLE, correct_indices=[0, 0]), "INVALID_QUESTION_OPTIONS"),
        (replace(MULTIPLE, options=["A", "A", "C"]), "INVALID_QUESTION_OPTIONS"),
        (replace(MULTIPLE, kind="alternatives"), "INVALID_QUESTION_OPTIONS"),
        (replace(MULTIPLE, kind="true_false", correct_indices=[0]), "INVALID_QUESTION_OPTIONS"),
        (replace(DEVELOPMENT, rubric=None), "INVALID_DEVELOPMENT_KEY"),
    ):
        with pytest.raises(IntegrityError, match=error), postgres.begin_nested():
            _publish(postgres, course, generation, [MULTIPLE, invalid])
        assert list_exams(postgres, course) == []
        assert (
            postgres.scalar(
                select(generation_runs.c.status).where(
                    generation_runs.c.id == generation,
                )
            )
            == "running"
        )

    assert _publish(postgres, course, generation, [MULTIPLE])
    with pytest.raises(DataError, match="INVALID_EXAM_GENERATION"), postgres.begin_nested():
        _publish(postgres, course, generation, [MULTIPLE])


def test_exam_generation_must_belong_to_the_owner_and_course(postgres, exam_context):
    owner, peer, course, other_course = exam_context
    generation = _generation(postgres, course)
    wrong_kind = _generation(postgres, course, kind="chat")
    missing_request = _generation(postgres, course, record=False)
    for invalid in (wrong_kind, missing_request):
        with pytest.raises(DataError, match="INVALID_EXAM_GENERATION"), postgres.begin_nested():
            _publish(postgres, course, invalid, [MULTIPLE])
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        _publish(postgres, other_course, generation, [MULTIPLE])
    _role(postgres, peer)
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        _publish(postgres, course, generation, [MULTIPLE])

    _role(postgres, owner)
    exam = _publish(postgres, course, generation, [MULTIPLE])
    _role(postgres, peer)
    assert list_exams(postgres, course) == []
    assert list_exam_questions(postgres, course, exam) == []


def test_deleting_a_generation_source_retires_exam_and_clears_keys(postgres, exam_context):
    owner, _, course, _ = exam_context
    postgres.exec_driver_sql("RESET ROLE")
    space = register_embedding_space(
        postgres,
        provider="ollama",
        model_name="test-model",
        model_revision=uuid4().hex,
        dimension=2,
    )
    _role(postgres, owner)
    source = "Contenido de la fuente"
    document = register_upload(
        postgres,
        course_id=course,
        filename="fuente.md",
        data=source.encode(),
        mime_type="text/markdown",
    )
    _role(postgres, owner, worker=True)
    run = start_processing(
        postgres,
        course,
        document,
        embedding_space_id=space,
        parser_version="v1",
        chunker_version="v1",
        parameters={},
    )
    assert publish_processing(
        postgres,
        run,
        extracted_text=source,
        parents=[ParentChunk(source, [], {}, 4)],
        children=[ChildChunk(0, source, source, 0, len(source), 4, [1, 0])],
    )
    _role(postgres, owner)
    generation = _generation(
        postgres,
        course,
        sources=search_course_chunks(postgres, course, space, [1, 0]),
    )
    exam = _publish(postgres, course, generation, [MULTIPLE])
    (question,) = list_exam_questions(postgres, course, exam)

    assert delete_document(postgres, course, document)
    (retired,) = list_exams(postgres, course)
    assert (retired["status"], retired["title"]) == ("invalidated", "Evaluación retirada")
    assert retired["generation_id"] == generation
    assert list_exam_questions(postgres, course, exam) == []

    postgres.exec_driver_sql("RESET ROLE")
    for table in (question_keys, correct_options, question_options):
        assert not postgres.execute(
            select(table).where(table.c.question_id == question["id"])
        ).first()
