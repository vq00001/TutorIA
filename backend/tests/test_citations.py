from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.citations import Citation, append_cited_assistant_message, list_message_citations
from db.connection import set_user_context
from db.conversations import append_user_message, create_conversation, list_messages
from db.courses import create_course, memberships
from db.documents import delete_document, register_upload
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

SOURCE = "Primer 🧪\nSegunda fuente."
REQUEST = b'{"model":"local","messages":[{"role":"user","content":"Question"}]}'
QUESTION = [{"type": "text", "text": "¿Qué dicen las fuentes?"}]
ANSWER = [{"type": "text", "text": "Respuesta con fuentes [1] y [2]."}]


def _role(conn, actor, *, worker=False):
    conn.exec_driver_sql(
        "SET LOCAL ROLE tutoria_worker" if worker else "SET LOCAL ROLE tutoria_api"
    )
    set_user_context(conn, actor)


@pytest.fixture
def citation_context(postgres):
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
    space = register_embedding_space(
        postgres,
        provider="ollama",
        model_name="search-model",
        model_revision=uuid4().hex,
        dimension=2,
    )
    _role(postgres, owner)
    course = create_course(postgres, name="Clase")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="student"))
    _role(postgres, owner)
    document = register_upload(
        postgres,
        course_id=course,
        filename="fuente.md",
        data=SOURCE.encode(),
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
        extracted_text=SOURCE,
        parents=[ParentChunk("Primer 🧪", [], {}, 3), ParentChunk("Segunda fuente.", [], {}, 3)],
        children=[
            ChildChunk(0, "🧪", "🧪", 7, 8, 1, [1, 0]),
            ChildChunk(1, "Segunda", "Segunda", 9, 16, 1, [1, 1]),
        ],
    )
    _role(postgres, owner)
    sources = search_course_chunks(postgres, course, space, [1, 0])
    conversation = create_conversation(postgres, course, "Chat")
    question = append_user_message(postgres, course, conversation, QUESTION)
    return course, document, conversation, question, peer, sources


def _generation(conn, course):
    return start_generation(
        conn,
        course,
        kind="chat",
        provider="ollama",
        model_name="local",
        model_revision="sha",
        prompt_version="v1",
    )


def test_citations_use_the_selected_request_and_exact_child_offsets(postgres, citation_context):
    course, _, conversation, question, peer, sources = citation_context
    generation = _generation(postgres, course)
    record_generation_request(postgres, generation, REQUEST, sources)
    selected = record_generation_request(postgres, generation, REQUEST, sources[::-1])

    response = append_cited_assistant_message(
        postgres,
        course,
        conversation,
        ANSWER,
        generation_id=generation,
        in_reply_to=question,
        request_id=selected,
        references=[Citation(2, "Primera cita"), Citation(1, "Segunda cita")],
    )
    rows = list_message_citations(postgres, response)
    assert [(row["ordinal"], row["source_rank"]) for row in rows] == [(1, 2), (2, 1)]
    for citation, source in zip(rows, sources, strict=True):
        assert citation["request_id"] == selected
        assert citation["parent_id"] == source["parent_id"]
        assert citation["child_id"] == source["child_id"]
        assert citation["start_offset"] == source["start_offset"]
        assert citation["end_offset"] == source["end_offset"]
        assert SOURCE[citation["start_offset"] : citation["end_offset"]] == source["child_text"]
    assert list_messages(postgres, course, conversation)[1]["generation_id"] == generation

    _role(postgres, peer)
    assert list_message_citations(postgres, response) == []


def test_invalid_citation_rolls_back_the_response_and_generation_completion(
    postgres,
    citation_context,
):
    course, _, conversation, question, _, sources = citation_context
    generation = _generation(postgres, course)
    request = record_generation_request(postgres, generation, REQUEST, sources[:1])
    another_generation = _generation(postgres, course)
    another_request = record_generation_request(postgres, another_generation, REQUEST, sources)

    for selected, rank, error in (
        (another_request, 1, "INVALID_CITATION_REQUEST"),
        (request, 2, "INVALID_CITATION_SOURCE"),
    ):
        with pytest.raises(IntegrityError, match=error), postgres.begin_nested():
            append_cited_assistant_message(
                postgres,
                course,
                conversation,
                ANSWER,
                generation_id=generation,
                in_reply_to=question,
                request_id=selected,
                references=[Citation(1, "Válida"), Citation(rank, "Inválida")],
            )
    assert len(list_messages(postgres, course, conversation)) == 1
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == generation,
            )
        )
        == "running"
    )

    response = append_cited_assistant_message(
        postgres,
        course,
        conversation,
        ANSWER,
        generation_id=generation,
        in_reply_to=question,
        request_id=request,
        references=[Citation(1, "Válida")],
    )
    assert len(list_message_citations(postgres, response)) == 1


def test_deleting_source_keeps_only_an_unlinked_citation_placeholder(postgres, citation_context):
    course, document, conversation, question, _, sources = citation_context
    generation = _generation(postgres, course)
    request = record_generation_request(postgres, generation, REQUEST, sources)
    response = append_cited_assistant_message(
        postgres,
        course,
        conversation,
        ANSWER,
        generation_id=generation,
        in_reply_to=question,
        request_id=request,
        references=[Citation(1, "Texto de la fuente")],
    )

    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.exec_driver_sql("DELETE FROM app.citations")
    assert delete_document(postgres, course, document)

    (citation,) = list_message_citations(postgres, response)
    assert (citation["ordinal"], citation["status"], citation["label"]) == (
        1,
        "source_removed",
        "Fuente retirada",
    )
    for key in (
        "request_id",
        "source_rank",
        "document_id",
        "parent_id",
        "child_id",
        "start_offset",
        "end_offset",
    ):
        assert citation[key] is None
    (_, redacted) = list_messages(postgres, course, conversation)
    assert redacted["status"] == "redacted"
    assert redacted["parts"] == []
