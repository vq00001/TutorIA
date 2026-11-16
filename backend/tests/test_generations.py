from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.courses import create_course, memberships
from db.documents import delete_document, register_upload
from db.generations import (
    generation_requests,
    generation_runs,
    get_generation_request,
    record_generation_request,
    start_generation,
)
from db.processing import (
    ChildChunk,
    ParentChunk,
    publish_processing,
    register_embedding_space,
    start_processing,
)
from db.users import users
from db.vector_search import search_course_chunks

BODY = '{ "model": "local",\r\n "messages": [{"content": "Contexto 🧪\\n", "role": "user"}] }\n'.encode()


def _role(conn, actor, *, worker=False):
    conn.exec_driver_sql(
        "SET LOCAL ROLE tutoria_worker" if worker else "SET LOCAL ROLE tutoria_api"
    )
    set_user_context(conn, actor)


@pytest.fixture
def generation_context(postgres):
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
    course = create_course(postgres, name="Curso")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="student"))
    _role(postgres, owner)
    return owner, peer, course, space


def _publish_source(conn, owner, course, space):
    source = "A 🧪\nSegunda fuente."
    document = register_upload(
        conn,
        course_id=course,
        filename="guia.md",
        data=source.encode(),
        mime_type="text/markdown",
    )
    _role(conn, owner, worker=True)
    run = start_processing(
        conn,
        course,
        document,
        embedding_space_id=space,
        parser_version="v1",
        chunker_version="v1",
        parameters={},
    )
    assert publish_processing(
        conn,
        run,
        extracted_text=source,
        parents=[ParentChunk("A 🧪", [], {}, 3), ParentChunk("Segunda fuente.", [], {}, 3)],
        children=[
            ChildChunk(0, "🧪", "🧪", 2, 3, 1, [1, 0]),
            ChildChunk(1, "Segunda", "Segunda", 4, 11, 1, [1, 1]),
        ],
    )
    _role(conn, owner)
    return document, search_course_chunks(conn, course, space, [1, 0])


def _start(conn, course):
    return start_generation(
        conn,
        course,
        kind="chat",
        provider="ollama",
        model_name="local",
        model_revision="sha",
        prompt_version="v1",
    )


def test_generation_preserves_exact_request_bytes_and_ordered_sources(postgres, generation_context):
    owner, _, course, space = generation_context
    _, sources = _publish_source(postgres, owner, course, space)
    generation = _start(postgres, course)

    request = record_generation_request(postgres, generation, BODY, sources[::-1])
    result = get_generation_request(postgres, request)

    assert result["request_body"] == BODY
    assert result["generation_id"] == generation
    assert [row["rank"] for row in result["sources"]] == [1, 2]
    for stored, hit in zip(result["sources"], sources[::-1], strict=True):
        for key in ("document_id", "run_id", "parent_id", "child_id", "embedding_space_id"):
            assert stored[key] == str(hit[key])
        for key in ("start_offset", "end_offset", "score"):
            assert stored[key] == hit[key]
    empty = record_generation_request(postgres, generation, BODY, [])
    assert get_generation_request(postgres, empty)["sources"] == []


def test_forged_offsets_and_cross_course_sources_roll_back(postgres, generation_context):
    owner, _, course, space = generation_context
    _, sources = _publish_source(postgres, owner, course, space)
    other_course = create_course(postgres, name="Otra clase")
    _, other_sources = _publish_source(postgres, owner, other_course, space)
    generation = _start(postgres, course)
    forged = {**sources[0], "start_offset": sources[0]["start_offset"] + 1}

    for invalid in (forged, other_sources[0], {**sources[0], "parent_id": uuid4()}):
        with (
            pytest.raises(IntegrityError, match="INVALID_GENERATION_SOURCE"),
            postgres.begin_nested(),
        ):
            record_generation_request(postgres, generation, BODY, [invalid])
    with pytest.raises(DataError, match="INVALID_GENERATION_REQUEST"), postgres.begin_nested():
        record_generation_request(postgres, generation, b'{"prompt":"otro formato"}', [])
    assert (
        postgres.scalar(
            select(func.count())
            .select_from(generation_requests)
            .where(
                generation_requests.c.generation_id == generation,
            )
        )
        == 0
    )


def test_generation_is_private_even_between_members_of_the_same_course(
    postgres, generation_context
):
    _, peer, course, _ = generation_context
    generation = _start(postgres, course)
    request = record_generation_request(postgres, generation, BODY, [])

    _role(postgres, peer)
    assert get_generation_request(postgres, request) is None
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        record_generation_request(postgres, generation, BODY, [])


def test_deleting_a_source_removes_the_generation_requests_and_preserves_unrelated_logs(
    postgres,
    generation_context,
):
    owner, _, course, space = generation_context
    document, sources = _publish_source(postgres, owner, course, space)
    affected = _start(postgres, course)
    first = record_generation_request(postgres, affected, BODY, sources)
    retry = record_generation_request(postgres, affected, BODY, [])
    unrelated = _start(postgres, course)
    unrelated_request = record_generation_request(postgres, unrelated, BODY, [])

    assert delete_document(postgres, course, document)

    assert get_generation_request(postgres, first) is None
    assert get_generation_request(postgres, retry) is None
    assert get_generation_request(postgres, unrelated_request)["request_body"] == BODY
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == affected,
            )
        )
        == "invalidated"
    )
    with pytest.raises(DataError, match="GENERATION_NOT_ACTIVE"), postgres.begin_nested():
        record_generation_request(postgres, affected, BODY, [])
