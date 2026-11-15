from uuid import uuid4

import pytest
from . import test_exams
from . import test_vector_search
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from .test_exams import _role
from .test_vector_search import _as_role, _publish, _start, _upload

from db.courses import courses, create_course
from db.documents import delete_document
from db.learning_catalog import (
    add_concept_alias,
    add_concept_source,
    add_prerequisite,
    add_prerequisite_source,
    concept_sources,
    create_concept,
    edge_sources,
    list_concept_aliases,
    list_concept_sources,
    list_concepts,
    list_prerequisite_sources,
    list_prerequisites,
)
from db.processing import parent_chunks

catalog_context = test_exams.exam_context
source_context = test_vector_search.search_context


def test_catalog_normalizes_keys_and_scopes_reads_and_writes(postgres, catalog_context):
    owner, student, course, other = catalog_context
    concept = create_concept(
        postgres, course, label="  A\u0301rbol   binario  ", description="Estructura"
    )
    assert create_concept(postgres, other, label="Árbol binario") != concept
    assert add_concept_alias(postgres, course, concept, "  AB\t de búsqueda  ") == "ab de búsqueda"

    with pytest.raises(IntegrityError), postgres.begin_nested():
        create_concept(postgres, course, label="ÁRBOL binario")
    with pytest.raises(IntegrityError), postgres.begin_nested():
        add_concept_alias(postgres, course, concept, "ab de búsqueda")
    with pytest.raises(IntegrityError), postgres.begin_nested():
        add_concept_alias(postgres, course, concept, "\t ")
    with pytest.raises(IntegrityError), postgres.begin_nested():
        add_concept_alias(postgres, other, concept, "otra clase")

    _role(postgres, student)
    (row,) = list_concepts(postgres, course)
    assert (row["id"], row["canonical_key"], row["description"]) == (
        concept,
        "árbol binario",
        "Estructura",
    )
    assert list_concepts(postgres, other) == []
    (alias,) = list_concept_aliases(postgres, course, concept)
    assert alias["alias"] == "  AB\t de búsqueda  "
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        create_concept(postgres, course, label="Sin permiso")

    _role(postgres, owner)
    postgres.execute(update(courses).where(courses.c.id == course).values(status="archived"))
    assert [row["id"] for row in list_concepts(postgres, course)] == [concept]
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        add_concept_alias(postgres, course, concept, "Archivada")


def test_prerequisites_are_directed_and_acyclic(postgres, catalog_context):
    _, _, course, other = catalog_context
    a = create_concept(postgres, course, label="A")
    b = create_concept(postgres, course, label="B")
    c = create_concept(postgres, course, label="C")
    outside = create_concept(postgres, other, label="D")
    add_prerequisite(postgres, course, a, b)
    expected = add_prerequisite(postgres, course, b, c)
    for source, target in ((a, a), (c, a), (b, a)):
        with pytest.raises(IntegrityError, match="PREREQUISITE_CYCLE"), postgres.begin_nested():
            add_prerequisite(postgres, course, source, target)
    for source, target in ((a, outside), (outside, a), (a, b)):
        with pytest.raises(IntegrityError), postgres.begin_nested():
            add_prerequisite(postgres, course, source, target)
    (edge,) = list_prerequisites(postgres, course, c)
    assert (edge["id"], edge["source_id"], edge["target_id"]) == (expected, b, c)
    assert list_prerequisites(postgres, course, a) == []


def _publish_parent(conn, actor, course, document, space, text, *, chunker="v1"):
    run = _start(conn, actor, course, document, space, chunker=chunker)
    _publish(conn, run, text, [text], [(0, text, [1, 1])])
    conn.exec_driver_sql("RESET ROLE")
    parent = conn.execute(
        select(parent_chunks.c.id).where(parent_chunks.c.run_id == run)
    ).scalar_one()
    _as_role(conn, actor)
    return run, parent


def test_catalog_sources_follow_active_processing_and_document_deletion(postgres, source_context):
    owner, outsider, course, space, _ = source_context
    other = create_course(postgres, name="Otra clase")
    text = "Definición"
    document = _upload(postgres, owner, course, text)
    run, parent = _publish_parent(postgres, owner, course, document, space, text)
    first = create_concept(postgres, course, label="Fundamentos")
    second = create_concept(postgres, course, label="Aplicaciones")
    edge = add_prerequisite(postgres, course, first, second)
    outside = create_concept(postgres, other, label="Fuera de la clase")

    add_concept_source(postgres, course, second, parent)
    add_prerequisite_source(postgres, course, edge, parent)
    for rows in (
        list_concept_sources(postgres, course, second),
        list_prerequisite_sources(postgres, course, edge),
    ):
        (source,) = rows
        assert (source["parent_id"], source["document_id"], source["run_id"]) == (
            parent,
            document,
            run,
        )

    for target_course, concept, fragment in ((other, outside, parent), (course, second, uuid4())):
        with pytest.raises(IntegrityError, match="INVALID_CATALOG_SOURCE"), postgres.begin_nested():
            add_concept_source(postgres, target_course, concept, fragment)
    _as_role(postgres, outsider)
    assert list_concept_sources(postgres, course, second) == []
    assert list_prerequisite_sources(postgres, course, edge) == []
    _as_role(postgres, owner)

    _, current_parent = _publish_parent(
        postgres, owner, course, document, space, text, chunker="v2"
    )
    assert list_concept_sources(postgres, course, second) == []
    assert list_prerequisite_sources(postgres, course, edge) == []
    with pytest.raises(IntegrityError, match="INVALID_CATALOG_SOURCE"), postgres.begin_nested():
        add_prerequisite_source(postgres, course, edge, parent)
    add_concept_source(postgres, course, second, current_parent)
    assert delete_document(postgres, course, document)
    assert list_concept_sources(postgres, course, second) == []
    assert len(list_concepts(postgres, course)) == 2
    assert len(list_prerequisites(postgres, course, second)) == 1

    postgres.exec_driver_sql("RESET ROLE")
    for table in (concept_sources, edge_sources):
        assert postgres.execute(select(table).where(table.c.course_id == course)).first() is None
