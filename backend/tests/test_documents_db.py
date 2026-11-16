from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.courses import courses, create_course, memberships, update_course
from db.documents import (
    DuplicateDocumentWarning,
    delete_document,
    document_versions,
    documents,
    download_document,
    file_contents,
    list_documents,
    register_upload,
)
from db.users import users


@pytest.fixture
def course_context(postgres):
    owner, student, outsider = uuid4(), uuid4(), uuid4()
    postgres.execute(
        insert(users),
        [
            {
                "id": uid,
                "nickname": f"u_{uid.hex}",
                "display_name": "Synthetic",
                "email": f"{uid.hex}@example.test",
                "password_hash": "test-only",
                "status": "active",
            }
            for uid in (owner, student, outsider)
        ],
    )
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(postgres, owner)
    course = create_course(postgres, name="Curso")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(postgres, student)
    return course, owner, student, outsider


def _upload(conn, course, data, filename="guia.pdf", **metadata):
    return register_upload(
        conn,
        course_id=course,
        filename=filename,
        data=data,
        mime_type="application/pdf",
        **metadata,
    )


def test_upload_stores_original_bytes_and_lists_metadata(postgres, course_context):
    course, _, student, _ = course_context
    data = b"original\x00\xff"
    identifier = _upload(
        postgres,
        course,
        data,
        title="Guía",
        authors=["Ana"],
        subject="Cálculo",
    )
    (item,) = list_documents(postgres, course, limit=10)
    assert item["id"] == identifier
    assert item["uploaded_by"] == student
    assert item["title"] == "Guía"
    assert item["authors"] == ["Ana"]
    assert item["subject"] == "Cálculo"
    assert item["original_name"] == "guia.pdf"
    assert item["mime_type"] == "application/pdf"
    assert item["size_bytes"] == len(data)
    assert item["upload_sha256"] == sha256(data).digest()
    assert item["processing_status"] == "uploaded"
    assert "bytes" not in item

    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(file_contents.c.bytes))
    postgres.exec_driver_sql("RESET ROLE")
    assert (
        postgres.scalar(
            select(file_contents.c.bytes).where(file_contents.c.document_id == identifier)
        )
        == data
    )


def test_duplicate_is_scoped_to_class_and_a_correction_is_independent(postgres, course_context):
    course, owner, _, _ = course_context
    first = _upload(postgres, course, b"contenido")
    set_user_context(postgres, owner)
    with pytest.warns(DuplicateDocumentWarning, match="Archivo duplicado descartado"):
        assert _upload(postgres, course, b"contenido", "renombrado.pdf") is None

    corrected = _upload(postgres, course, b"contenido corregido")
    assert corrected != first
    other_course = create_course(postgres, name="Otro curso")
    other_document = _upload(postgres, other_course, b"contenido")
    assert other_document not in (first, corrected)
    assert {row["id"] for row in list_documents(postgres, course, limit=10)} == {first, corrected}

    postgres.exec_driver_sql("RESET ROLE")
    for table in (documents, document_versions, file_contents):
        assert (
            postgres.scalar(
                select(func.count()).select_from(table).where(table.c.course_id == course)
            )
            == 2
        )


def test_listing_paginates_and_hides_withdrawn_documents(postgres, course_context):
    course, _, _, _ = course_context
    first = _upload(postgres, course, b"uno")
    second = _upload(postgres, course, b"dos")
    withdrawn = _upload(postgres, course, b"retirado")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(documents).where(documents.c.id == withdrawn).values(status="deletion_pending")
    )
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    (page_one,) = list_documents(postgres, course, limit=1)
    (page_two,) = list_documents(
        postgres,
        course,
        limit=1,
        after=(page_one["created_at"], page_one["id"]),
    )
    assert {page_one["id"], page_two["id"]} == {first, second}
    assert not postgres.execute(
        select(document_versions.c.id).where(document_versions.c.document_id == withdrawn)
    ).first()


def test_outsider_cannot_see_or_upload_and_archived_course_is_read_only(postgres, course_context):
    course, owner, _, outsider = course_context
    _upload(postgres, course, b"contenido")
    set_user_context(postgres, outsider)
    assert list_documents(postgres, course, limit=10) == []
    assert not postgres.execute(
        select(document_versions.c.id).where(
            document_versions.c.course_id == course,
        )
    ).first()
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        _upload(postgres, course, b"contenido")

    set_user_context(postgres, owner)
    assert update_course(postgres, course, 1, status="archived")
    assert len(list_documents(postgres, course, limit=10)) == 1
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        _upload(postgres, course, b"archivo nuevo")


def test_invalid_upload_does_not_leave_a_document_without_its_file(postgres, course_context):
    course, _, _, _ = course_context
    with (
        pytest.raises(IntegrityError, match="document_versions_valid_size"),
        postgres.begin_nested(),
    ):
        _upload(postgres, course, b"")
    assert list_documents(postgres, course, limit=10) == []
    assert (
        postgres.scalar(
            select(func.count())
            .select_from(documents)
            .where(
                documents.c.course_id == course,
            )
        )
        == 0
    )


def test_download_returns_original_to_members_and_hides_it_from_outsiders(postgres, course_context):
    course, owner, _, outsider = course_context
    payload = b"archivo original\x00\xff"
    identifier = _upload(postgres, course, payload, "Guía.pdf")

    set_user_context(postgres, owner)
    result = download_document(postgres, course, identifier)
    assert dict(result) == {
        "original_name": "Guía.pdf",
        "mime_type": "application/pdf",
        "size_bytes": len(payload),
        "bytes": payload,
    }
    other_course = create_course(postgres, name="Otro curso")
    assert download_document(postgres, other_course, identifier) is None

    set_user_context(postgres, outsider)
    assert download_document(postgres, course, identifier) is None


@pytest.mark.parametrize("state", ["purged", "deletion_pending"])
def test_download_hides_purged_or_withdrawn_files(postgres, course_context, state):
    course, _, _, _ = course_context
    identifier = _upload(postgres, course, b"contenido")
    postgres.exec_driver_sql("RESET ROLE")
    if state == "purged":
        postgres.execute(
            update(document_versions)
            .where(
                document_versions.c.document_id == identifier,
            )
            .values(status=state)
        )
    else:
        postgres.execute(update(documents).where(documents.c.id == identifier).values(status=state))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")

    assert download_document(postgres, course, identifier) is None


@pytest.mark.parametrize("actor", ["uploader", "coordinator"])
def test_delete_removes_content_and_releases_hash(postgres, course_context, actor):
    course, owner, _, _ = course_context
    payload = b"contenido"
    identifier = _upload(postgres, course, payload)
    if actor == "coordinator":
        set_user_context(postgres, owner)
    initial_revision = postgres.scalar(
        select(courses.c.corpus_revision).where(courses.c.id == course)
    )

    assert delete_document(postgres, course, identifier)
    assert not delete_document(postgres, course, identifier)
    assert list_documents(postgres, course, limit=10) == []
    assert download_document(postgres, course, identifier) is None
    assert (
        postgres.scalar(
            select(courses.c.corpus_revision).where(
                courses.c.id == course,
            )
        )
        == initial_revision + 1
    )

    postgres.exec_driver_sql("RESET ROLE")
    for table in (documents, document_versions, file_contents):
        assert (
            postgres.scalar(
                select(func.count())
                .select_from(table)
                .where(
                    table.c.course_id == course,
                )
            )
            == 0
        )
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    assert _upload(postgres, course, payload) not in (None, identifier)


def test_other_students_cannot_delete_and_archived_courses_allow_only_download(
    postgres,
    course_context,
):
    course, owner, student, outsider = course_context
    set_user_context(postgres, owner)
    identifier = _upload(postgres, course, "contenido de la coordinación".encode())
    set_user_context(postgres, student)
    with pytest.raises(ProgrammingError, match="FORBIDDEN"), postgres.begin_nested():
        delete_document(postgres, course, identifier)
    set_user_context(postgres, outsider)
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        delete_document(postgres, course, identifier)

    set_user_context(postgres, owner)
    assert update_course(postgres, course, 1, status="archived")
    assert (
        download_document(postgres, course, identifier)["bytes"]
        == "contenido de la coordinación".encode()
    )
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        delete_document(postgres, course, identifier)


def test_rollback_preserves_original_and_hash(postgres, course_context):
    course, _, _, _ = course_context
    payload = b"contenido"
    identifier = _upload(postgres, course, payload)

    savepoint = postgres.begin_nested()
    assert delete_document(postgres, course, identifier)
    savepoint.rollback()

    assert download_document(postgres, course, identifier)["bytes"] == payload
    with pytest.warns(DuplicateDocumentWarning):
        assert _upload(postgres, course, payload) is None
