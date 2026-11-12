from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.conversations import (
    append_assistant_message,
    append_user_message,
    create_conversation,
    list_conversations,
    list_messages,
    messages,
)
from db.courses import create_course, memberships, update_course
from db.generations import (
    generation_runs,
    get_generation_request,
    record_generation_request,
    start_generation,
    stop_generation,
)
from db.users import users

QUESTION = [{"type": "text", "text": "¿Cómo se calcula?"}]
ANSWER = [{"type": "text", "text": "Esta es la respuesta."}]
REQUEST = b'{"model":"local","messages":[{"role":"user","content":"Question"}]}'


def _as_user(conn, actor):
    conn.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(conn, actor)


@pytest.fixture
def history_context(postgres):
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
    _as_user(postgres, owner)
    course = create_course(postgres, name="Clase")
    other_course = create_course(postgres, name="Otra clase")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="student"))
    _as_user(postgres, owner)
    return owner, peer, course, other_course


def _generation(conn, course, *, kind="chat", record=True):
    generation = start_generation(
        conn,
        course,
        kind=kind,
        provider="ollama",
        model_name="local",
        model_revision="sha",
        prompt_version="v1",
    )
    request = record_generation_request(conn, generation, REQUEST, []) if record else None
    return generation, request


def test_history_is_private_by_user_and_course(postgres, history_context):
    owner, peer, course, other_course = history_context
    first = create_conversation(postgres, course, "Primera")
    append_user_message(postgres, course, first, QUESTION)
    second = create_conversation(postgres, course, "Segunda")
    create_conversation(postgres, other_course, "Clase distinta")

    (recent,) = list_conversations(postgres, course, limit=1)
    assert recent["id"] == second
    (older,) = list_conversations(
        postgres,
        course,
        limit=1,
        before=(recent["updated_at"], recent["id"]),
    )
    assert older["id"] == first
    assert list_messages(postgres, other_course, first) == []

    _as_user(postgres, peer)
    assert list_conversations(postgres, course) == []
    assert list_messages(postgres, course, first) == []
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        append_user_message(postgres, course, first, QUESTION)

    _as_user(postgres, owner)
    assert update_course(postgres, course, 1, status="archived")
    assert list_messages(postgres, course, first)[0]["parts"] == QUESTION
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        append_user_message(postgres, course, first, QUESTION)


def test_response_links_question_and_generation_and_completes_it(postgres, history_context):
    _, _, course, _ = history_context
    conversation = create_conversation(postgres, course, "Chat")
    question = append_user_message(postgres, course, conversation, QUESTION)
    generation, _ = _generation(postgres, course)

    answer = append_assistant_message(
        postgres,
        course,
        conversation,
        ANSWER,
        generation_id=generation,
        in_reply_to=question,
    )
    (first,) = list_messages(postgres, course, conversation, limit=1)
    (second,) = list_messages(postgres, course, conversation, after_sequence=first["sequence_no"])
    assert (first["id"], first["role"], first["sequence_no"]) == (question, "user", 1)
    assert (second["id"], second["role"], second["sequence_no"]) == (answer, "assistant", 2)
    assert second["generation_id"] == generation
    assert second["in_reply_to"] == question
    assert second["parts"] == ANSWER
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == generation,
            )
        )
        == "completed"
    )
    with pytest.raises(DataError, match="GENERATION_NOT_ACTIVE"), postgres.begin_nested():
        record_generation_request(postgres, generation, REQUEST, [])
    with pytest.raises(IntegrityError, match="INVALID_REPLY_GENERATION"), postgres.begin_nested():
        append_assistant_message(
            postgres,
            course,
            conversation,
            ANSWER,
            generation_id=generation,
            in_reply_to=question,
        )


def test_response_rejects_wrong_scope_or_missing_model_request(postgres, history_context):
    owner, peer, course, other_course = history_context
    conversation = create_conversation(postgres, course, "Chat")
    other_conversation = create_conversation(postgres, course, "Otro chat")
    question = append_user_message(postgres, course, conversation, QUESTION)
    other_question = append_user_message(postgres, course, other_conversation, QUESTION)
    valid, _ = _generation(postgres, course)
    _as_user(postgres, peer)
    wrong_owner, _ = _generation(postgres, course)
    _as_user(postgres, owner)

    with pytest.raises(IntegrityError, match="INVALID_REPLY_TARGET"), postgres.begin_nested():
        append_assistant_message(
            postgres,
            course,
            conversation,
            ANSWER,
            generation_id=valid,
            in_reply_to=other_question,
        )

    for target_course, kind, record in (
        (other_course, "chat", True),
        (course, "chat", False),
        (course, "exam", True),
    ):
        invalid, _ = _generation(postgres, target_course, kind=kind, record=record)
        with (
            pytest.raises(IntegrityError, match="INVALID_REPLY_GENERATION"),
            postgres.begin_nested(),
        ):
            append_assistant_message(
                postgres, course, conversation, ANSWER, generation_id=invalid, in_reply_to=question
            )
        assert stop_generation(postgres, invalid, status="cancelled")

    with pytest.raises(IntegrityError, match="INVALID_REPLY_GENERATION"), postgres.begin_nested():
        append_assistant_message(
            postgres, course, conversation, ANSWER, generation_id=wrong_owner, in_reply_to=question
        )

    assert len(list_messages(postgres, course, conversation)) == 1
    with pytest.raises(IntegrityError, match="messages_parts"), postgres.begin_nested():
        append_assistant_message(
            postgres,
            course,
            conversation,
            [],
            generation_id=valid,
            in_reply_to=question,
        )
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == valid,
            )
        )
        == "running"
    )


def test_invalidating_generation_redacts_reply_but_keeps_sequence(postgres, history_context):
    owner, _, course, _ = history_context
    conversation = create_conversation(postgres, course, "Chat")
    question = append_user_message(postgres, course, conversation, QUESTION)
    generation, request = _generation(postgres, course)
    answer = append_assistant_message(
        postgres,
        course,
        conversation,
        ANSWER,
        generation_id=generation,
        in_reply_to=question,
    )

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(generation_runs)
        .where(generation_runs.c.id == generation)
        .values(
            status="invalidated",
        )
    )
    _as_user(postgres, owner)
    first, second = list_messages(postgres, course, conversation)
    assert (first["id"], first["parts"]) == (question, QUESTION)
    assert (second["id"], second["sequence_no"], second["status"], second["parts"]) == (
        answer,
        2,
        "redacted",
        [],
    )
    assert second["redacted_at"] is not None
    assert get_generation_request(postgres, request) is None

    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(update(messages).where(messages.c.id == answer).values(parts=ANSWER))
