from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.conversations import append_assistant_message, append_user_message, create_conversation
from db.courses import create_course, memberships
from db.documents import delete_document, register_upload
from db.generated_materials import (
    GeneratedMaterial,
    get_generated_material,
    list_generated_materials,
    store_generated_materials,
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


def _role(conn, actor, *, worker=False):
    conn.exec_driver_sql(
        "SET LOCAL ROLE tutoria_worker" if worker else "SET LOCAL ROLE tutoria_api"
    )
    set_user_context(conn, actor)


@pytest.fixture
def material_context(postgres):
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


def _generation(conn, course, *, sources=(), record=True):
    generation = start_generation(
        conn,
        course,
        kind="chat",
        provider="ollama",
        model_name="local",
        model_revision="sha",
        prompt_version="v1",
    )
    if record:
        record_generation_request(conn, generation, REQUEST, sources)
    return generation


def test_material_batch_stores_versioned_json_and_retries_without_duplicates(
    postgres, material_context
):
    _, _, course, other_course = material_context
    generation = _generation(postgres, course)
    summary = GeneratedMaterial("Resumen", "summary", {"markdown": "Texto"}, schema_version=2)
    flashcards = GeneratedMaterial("Tarjetas", "flashcards", {"cards": [{"q": "Q", "a": "A"}]})
    materials = [summary, flashcards]

    expected = [summary.id, flashcards.id]
    assert store_generated_materials(postgres, course, generation, materials) == expected
    assert store_generated_materials(postgres, course, generation, materials) == expected
    stored = get_generated_material(postgres, course, summary.id)
    assert stored["title"] == summary.title
    assert stored["kind"] == summary.kind
    assert stored["content"] == summary.content
    assert stored["schema_version"] == 2
    assert stored["generation_id"] == generation
    assert get_generated_material(postgres, other_course, summary.id) is None

    (first,) = list_generated_materials(postgres, course, limit=1)
    (second,) = list_generated_materials(
        postgres,
        course,
        limit=1,
        before=(first["created_at"], first["id"]),
    )
    assert {first["id"], second["id"]} == set(expected)
    assert len(list_generated_materials(postgres, course)) == 2


def test_invalid_batch_and_conflicting_id_do_not_leave_partial_materials(
    postgres, material_context
):
    _, _, course, _ = material_context
    generation = _generation(postgres, course)
    valid = GeneratedMaterial("Resumen", "summary", {"markdown": "Texto"})
    invalid = GeneratedMaterial("Inválido", "explanation", {}, schema_version=0)

    with pytest.raises(IntegrityError, match="artifacts_schema_version"), postgres.begin_nested():
        store_generated_materials(postgres, course, generation, [valid, invalid])
    assert list_generated_materials(postgres, course) == []

    store_generated_materials(postgres, course, generation, [valid])
    another = GeneratedMaterial("Otro", "explanation", {"markdown": "Otro"})
    with (
        pytest.raises(IntegrityError, match="GENERATED_MATERIAL_CONFLICT"),
        postgres.begin_nested(),
    ):
        store_generated_materials(
            postgres, course, generation, [another, replace(valid, title="Cambio")]
        )
    assert get_generated_material(postgres, course, another.id) is None
    assert get_generated_material(postgres, course, valid.id)["title"] == valid.title


def test_materials_are_private_and_require_the_generation_in_the_same_course(
    postgres,
    material_context,
):
    owner, peer, course, other_course = material_context
    generation = _generation(postgres, course)
    material = GeneratedMaterial("Explicación", "explanation", {"markdown": "Texto"})
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        store_generated_materials(postgres, other_course, generation, [material])
    missing_request = _generation(postgres, course, record=False)
    with pytest.raises(DataError, match="GENERATION_NOT_AVAILABLE"), postgres.begin_nested():
        store_generated_materials(postgres, course, missing_request, [material])

    conversation = create_conversation(postgres, course, "Chat")
    question = append_user_message(
        postgres, course, conversation, [{"type": "text", "text": "Pregunta"}]
    )
    append_assistant_message(
        postgres,
        course,
        conversation,
        [{"type": "text", "text": "Respuesta"}],
        generation_id=generation,
        in_reply_to=question,
    )
    assert (
        postgres.scalar(
            select(generation_runs.c.status).where(
                generation_runs.c.id == generation,
            )
        )
        == "completed"
    )
    store_generated_materials(postgres, course, generation, [material])

    _role(postgres, peer)
    assert list_generated_materials(postgres, course) == []
    assert get_generated_material(postgres, course, material.id) is None
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        store_generated_materials(postgres, course, generation, [material])
    _role(postgres, owner)
    assert get_generated_material(postgres, course, material.id)["status"] == "active"


def test_deleting_generation_source_redacts_title_and_content(postgres, material_context):
    owner, _, course, _ = material_context
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
    sources = search_course_chunks(postgres, course, space, [1, 0])
    affected = _generation(postgres, course, sources=sources)
    unrelated = _generation(postgres, course)
    material = GeneratedMaterial(
        "Título derivado", "summary", {"markdown": source}, schema_version=3
    )
    untouched = GeneratedMaterial("Independiente", "summary", {"markdown": "Otro texto"})
    store_generated_materials(postgres, course, affected, [material])
    store_generated_materials(postgres, course, unrelated, [untouched])

    assert delete_document(postgres, course, document)
    redacted = get_generated_material(postgres, course, material.id)
    assert (redacted["status"], redacted["title"], redacted["content"]) == (
        "redacted",
        "Contenido retirado",
        {},
    )
    assert redacted["generation_id"] == affected
    assert redacted["schema_version"] == 3
    assert get_generated_material(postgres, course, untouched.id)["content"] == untouched.content
    with pytest.raises(DataError, match="GENERATION_NOT_AVAILABLE"), postgres.begin_nested():
        store_generated_materials(postgres, course, affected, [material])
