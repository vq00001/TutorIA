from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import Text, cast, func, insert, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.courses import courses, create_course
from db.documents import (
    delete_document,
    document_versions,
    documents,
    list_documents,
    register_upload,
)
from db.processing import (
    ChildChunk,
    ParentChunk,
    child_chunks,
    chunk_embeddings,
    fail_processing,
    get_processing_source,
    parent_chunks,
    processing_runs,
    publish_processing,
    register_embedding_space,
    start_processing,
)
from db.users import users

SOURCE = "Árbol 🧪 e\u0301\nOtra línea."
FRAGMENT = "🧪 e\u0301"
START = SOURCE.index(FRAGMENT)
END = START + len(FRAGMENT)
PARENT = ParentChunk(SOURCE, ["Sección"], {"line": 1}, 8)
CHILD = ChildChunk(0, FRAGMENT, f"Sección\n{FRAGMENT}", START, END, 3, [3.0, 4.0])


def _as_role(conn, role, user_id):
    if role == "worker":
        conn.exec_driver_sql("SET LOCAL ROLE tutoria_worker")
    else:
        conn.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(conn, user_id)


@pytest.fixture
def processing_context(postgres):
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
    model = {
        "provider": "ollama",
        "model_name": "test-embed",
        "model_revision": uuid4().hex,
        "dimension": 2,
        "document_prefix": "passage: ",
    }
    space = register_embedding_space(postgres, **model)
    assert register_embedding_space(postgres, **model) == space
    _as_role(postgres, "api", owner)
    course = create_course(postgres, name="Procesamiento")
    document = register_upload(
        postgres,
        course_id=course,
        filename="guia.md",
        data=SOURCE.encode(),
        mime_type="text/markdown",
    )
    _as_role(postgres, "worker", owner)
    return course, document, space, owner, outsider


def _start(conn, context, *, chunker="v1"):
    course, document, space, _, _ = context
    return start_processing(
        conn,
        course,
        document,
        embedding_space_id=space,
        parser_version="v1",
        chunker_version=chunker,
        parameters={"size": 64},
    )


def _publish(conn, run, child=CHILD):
    return publish_processing(conn, run, extracted_text=SOURCE, parents=[PARENT], children=[child])


def test_worker_publishes_exact_unicode_offsets_and_pgvector(postgres, processing_context):
    course, document, _, owner, _ = processing_context
    run = _start(postgres, processing_context)
    assert run is not None
    assert _start(postgres, processing_context) is None
    source = get_processing_source(postgres, run)
    assert source["bytes"] == SOURCE.encode()
    assert source["dimension"] == 2
    assert source["document_prefix"] == "passage: "

    _as_role(postgres, "api", owner)
    (before,) = list_documents(postgres, course, limit=10)
    assert before["processing_status"] == "processing"
    assert before["active_run_id"] is None
    _as_role(postgres, "worker", owner)
    assert _publish(postgres, run)
    assert not _publish(postgres, run)
    assert get_processing_source(postgres, run) is None
    assert _start(postgres, processing_context) is None

    postgres.exec_driver_sql("RESET ROLE")
    run_row = (
        postgres.execute(select(processing_runs).where(processing_runs.c.id == run))
        .mappings()
        .one()
    )
    assert run_row["state"] == "ready"
    child = (
        postgres.execute(select(child_chunks).where(child_chunks.c.run_id == run)).mappings().one()
    )
    assert (
        run_row["extracted_text"][child["start_offset"] : child["end_offset"]]
        == child["text"]
        == FRAGMENT
    )
    actual_type, dimension, norm = postgres.execute(
        select(
            cast(func.pg_typeof(chunk_embeddings.c.embedding), Text),
            func.vector_dims(chunk_embeddings.c.embedding),
            func.vector_norm(chunk_embeddings.c.embedding),
        ).where(chunk_embeddings.c.child_id == child["id"])
    ).one()
    assert (actual_type, dimension) == ("vector", 2)
    assert norm == pytest.approx(1.0)
    assert (
        postgres.scalar(select(documents.c.active_run_id).where(documents.c.id == document)) == run
    )
    assert postgres.scalar(select(courses.c.corpus_revision).where(courses.c.id == course)) == 1


@pytest.mark.parametrize(
    "child, error",
    [
        (replace(CHILD, start_offset=START - 1), "INVALID_CHILD_OFFSETS"),
        (replace(CHILD, embedding=[1.0, 2.0, 3.0]), "INVALID_EMBEDDING"),
        (replace(CHILD, parent_index=1), "INVALID_CHUNK_PARENT"),
    ],
)
def test_bad_result_rolls_back_every_fragment(postgres, processing_context, child, error):
    _, _, _, owner, _ = processing_context
    run = _start(postgres, processing_context)
    with pytest.raises(IntegrityError, match=error), postgres.begin_nested():
        _publish(postgres, run, child)

    postgres.exec_driver_sql("RESET ROLE")
    for table in (parent_chunks, child_chunks, chunk_embeddings):
        assert (
            postgres.scalar(select(func.count()).select_from(table).where(table.c.run_id == run))
            == 0
        )
    assert (
        postgres.scalar(select(processing_runs.c.state).where(processing_runs.c.id == run))
        == "running"
    )

    _as_role(postgres, "worker", owner)
    assert _publish(postgres, run)


def test_failed_reprocessing_preserves_published_run_and_can_retry(postgres, processing_context):
    course, _, _, owner, _ = processing_context
    first = _start(postgres, processing_context)
    assert fail_processing(postgres, first, "PARSER_FAILED")

    _as_role(postgres, "api", owner)
    assert list_documents(postgres, course, limit=10)[0]["processing_status"] == "failed"

    _as_role(postgres, "worker", owner)
    published = _start(postgres, processing_context)
    assert published != first
    assert _publish(postgres, published)
    replacement = _start(postgres, processing_context, chunker="v2")
    assert fail_processing(postgres, replacement, "EMBEDDING_FAILED")
    assert not _publish(postgres, replacement)

    _as_role(postgres, "api", owner)
    (item,) = list_documents(postgres, course, limit=10)
    assert (item["processing_status"], item["active_run_id"]) == ("ready", published)


def test_processing_permissions_and_delete_remove_derivatives(postgres, processing_context):
    course, document, _, owner, outsider = processing_context
    run = _start(postgres, processing_context)
    _as_role(postgres, "worker", outsider)
    assert get_processing_source(postgres, run) is None
    assert not _publish(postgres, run)
    assert not fail_processing(postgres, run, "FORBIDDEN")
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        _start(postgres, processing_context)

    _as_role(postgres, "api", owner)
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        get_processing_source(postgres, run)
    _as_role(postgres, "worker", owner)
    assert _publish(postgres, run)
    _as_role(postgres, "api", owner)
    assert delete_document(postgres, course, document)

    postgres.exec_driver_sql("RESET ROLE")
    for table in (
        processing_runs,
        parent_chunks,
        child_chunks,
        chunk_embeddings,
        document_versions,
    ):
        assert (
            postgres.scalar(
                select(func.count()).select_from(table).where(table.c.course_id == course)
            )
            == 0
        )
