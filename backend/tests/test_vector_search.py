from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DataError, ProgrammingError

from db.connection import set_user_context
from db.courses import create_course
from db.documents import delete_document, documents, register_upload
from db.processing import (
    ChildChunk,
    ParentChunk,
    child_chunks,
    chunk_embeddings,
    fail_processing,
    publish_processing,
    register_embedding_space,
    start_processing,
)
from db.users import users
from db.vector_search import search_course_chunks


def _as_role(conn, user_id, worker=False):
    if worker:
        conn.exec_driver_sql("SET LOCAL ROLE tutoria_worker")
    else:
        conn.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(conn, user_id)


@pytest.fixture
def search_context(postgres):
    owner, outsider = uuid4(), uuid4()
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
            for uid in (owner, outsider)
        ],
    )
    spaces = [
        register_embedding_space(
            postgres,
            provider="ollama",
            model_name="test-search",
            model_revision=uuid4().hex,
            dimension=2,
        )
        for _ in range(2)
    ]
    _as_role(postgres, owner)
    course = create_course(postgres, name="Búsqueda")
    return owner, outsider, course, *spaces


def _upload(conn, actor, course, content):
    _as_role(conn, actor)
    return register_upload(
        conn,
        course_id=course,
        filename="guia.md",
        data=content.encode(),
        mime_type="text/markdown",
    )


def _start(conn, actor, course, document, space, chunker="v1"):
    _as_role(conn, actor, worker=True)
    return start_processing(
        conn,
        course,
        document,
        embedding_space_id=space,
        parser_version="v1",
        chunker_version=chunker,
        parameters={},
    )


def _publish(conn, run, source, parent_texts, children):
    parents = [
        ParentChunk(text, [f"Sección {index}"], {"line": index}, 10)
        for index, text in enumerate(parent_texts)
    ]
    fragments = []
    for parent_index, fragment, embedding in children:
        start = source.index(fragment)
        fragments.append(
            ChildChunk(parent_index, fragment, fragment, start, start + len(fragment), 2, embedding)
        )
    assert publish_processing(conn, run, extracted_text=source, parents=parents, children=fragments)


def test_search_selects_one_winning_child_per_parent_before_limiting(postgres, search_context):
    owner, _, course, space, _ = search_context
    first = "Árbol 🧪 e\u0301. Otro hijo."
    second = "Segundo padre."
    source = f"{first}\n{second}"
    document = _upload(postgres, owner, course, source)
    run = _start(postgres, owner, course, document, space)
    winner = "🧪 e\u0301"
    _publish(
        postgres,
        run,
        source,
        [first, second],
        [
            (0, "Otro hijo", [4.0, 3.0]),
            (0, winner, [1.0, 0.0]),
            (1, "Segundo", [1.0, 1.0]),
        ],
    )
    _as_role(postgres, owner)

    best, other = search_course_chunks(postgres, course, space, [5.0, 0.0], limit=2)
    assert best["parent_id"] != other["parent_id"]
    assert (best["document_id"], best["run_id"], best["embedding_space_id"]) == (
        document,
        run,
        space,
    )
    assert best["parent_text"] == first and other["parent_text"] == second
    assert best["child_text"] == winner
    assert source[best["start_offset"] : best["end_offset"]] == winner
    assert best["section_path"] == ["Sección 0"] and best["locator"] == {"line": 0}
    assert best["score"] == pytest.approx(1)
    assert other["score"] == pytest.approx(2**-0.5)
    assert search_course_chunks(postgres, course, space, [1.0, 0.0], threshold=0.75) == [best]


def test_equal_scores_choose_the_lowest_child_identifier(postgres, search_context):
    owner, _, course, space, _ = search_context
    source = "Primero y segundo"
    document = _upload(postgres, owner, course, source)
    run = _start(postgres, owner, course, document, space)
    _publish(
        postgres,
        run,
        source,
        [source],
        [
            (0, "Primero", [1.0, 0.0]),
            (0, "segundo", [1.0, 0.0]),
        ],
    )

    postgres.exec_driver_sql("RESET ROLE")
    expected = postgres.scalar(
        select(child_chunks.c.id)
        .where(child_chunks.c.run_id == run)
        .order_by(child_chunks.c.id)
        .limit(1)
    )
    _as_role(postgres, owner)
    (hit,) = search_course_chunks(postgres, course, space, [1.0, 0.0])
    assert hit["child_id"] == expected


def test_search_is_scoped_to_membership_course_and_embedding_space(postgres, search_context):
    owner, outsider, course, space, other_space = search_context
    source = "Contenido del curso"
    document = _upload(postgres, owner, course, source)
    run = _start(postgres, owner, course, document, space)
    _publish(postgres, run, source, [source], [(0, source, [1.0, 0.0])])

    _as_role(postgres, owner)
    other_course = create_course(postgres, name="Otro curso")
    other_document = _upload(postgres, owner, other_course, source)
    other_run = _start(postgres, owner, other_course, other_document, space)
    _publish(postgres, other_run, source, [source], [(0, source, [1.0, 0.0])])

    _as_role(postgres, owner)
    assert [
        hit["document_id"] for hit in search_course_chunks(postgres, course, space, [1, 0])
    ] == [document]
    assert search_course_chunks(postgres, course, other_space, [1, 0]) == []
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(chunk_embeddings.c.embedding))

    _as_role(postgres, outsider)
    assert search_course_chunks(postgres, course, space, [1, 0]) == []
    assert search_course_chunks(postgres, course, uuid4(), [1, 0]) == []


def test_search_only_uses_active_run_and_excludes_withdrawn_documents(postgres, search_context):
    owner, _, course, space, _ = search_context
    document = _upload(postgres, owner, course, "Original")
    first = _start(postgres, owner, course, document, space)
    _publish(postgres, first, "Original", ["Original"], [(0, "Original", [1, 0])])
    pending = _start(postgres, owner, course, document, space, chunker="v2")
    _as_role(postgres, owner)
    assert [hit["run_id"] for hit in search_course_chunks(postgres, course, space, [1, 0])] == [
        first
    ]

    _as_role(postgres, owner, worker=True)
    assert fail_processing(postgres, pending, "PARSER_FAILED")
    current = _start(postgres, owner, course, document, space, chunker="v3")
    _publish(postgres, current, "Nuevo texto", ["Nuevo texto"], [(0, "Nuevo", [1, 0])])
    _as_role(postgres, owner)
    (hit,) = search_course_chunks(postgres, course, space, [1, 0])
    assert (hit["run_id"], hit["child_text"]) == (current, "Nuevo")

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(documents).where(documents.c.id == document).values(status="deletion_pending")
    )
    _as_role(postgres, owner)
    assert search_course_chunks(postgres, course, space, [1, 0]) == []
    assert delete_document(postgres, course, document)
    assert search_course_chunks(postgres, course, space, [1, 0]) == []


def test_search_rejects_invalid_parameters_and_vector_dimensions(postgres, search_context):
    _, _, course, space, _ = search_context
    with pytest.raises(ValueError, match="INVALID_SEARCH_PARAMETERS"):
        search_course_chunks(postgres, course, space, [1, 0], limit=0)
    with pytest.raises(ValueError, match="INVALID_SEARCH_PARAMETERS"):
        search_course_chunks(postgres, course, space, [1, 0], threshold=1.1)
    with pytest.raises(ValueError, match="INVALID_VECTOR"):
        search_course_chunks(postgres, course, space, [0, 0])
    with pytest.raises(DataError, match="INVALID_SEARCH_VECTOR"), postgres.begin_nested():
        search_course_chunks(postgres, course, space, [1, 0, 0])
    with pytest.raises(DataError, match="EMBEDDING_SPACE_MISMATCH"), postgres.begin_nested():
        search_course_chunks(postgres, course, uuid4(), [1, 0])
