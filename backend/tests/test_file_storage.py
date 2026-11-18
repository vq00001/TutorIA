from uuid import uuid4

from backend.storage import file_storage


def test_store_uploaded_document_uses_course_folder_and_preserves_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)
    course_id = uuid4()
    document_id = uuid4()
    content = b"\x00binary-content"

    saved = file_storage.store_uploaded_document(
        course_name="Cálculo III",
        course_id=course_id,
        document_id=document_id,
        filename="guía.md",
        content=content,
    )

    assert saved.parent == tmp_path / f"calculo-iii-{course_id.hex}"
    assert saved.name == f"{document_id.hex}_guía.md"
    assert saved.read_bytes() == content
    assert (
        file_storage.store_uploaded_document(
            course_name="Cálculo III",
            course_id=course_id,
            document_id=document_id,
            filename="guía.md",
            content=content,
        )
        == saved
    )


def test_store_uploaded_document_does_not_allow_filename_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)
    course_id = uuid4()
    saved = file_storage.store_uploaded_document(
        course_name="Curso",
        course_id=course_id,
        document_id=uuid4(),
        filename=r"..\..\otro\guia?.md",
        content=b"contenido",
    )

    assert saved.parent == tmp_path / f"curso-{course_id.hex}"
    assert saved.name.endswith("_guia_.md")
    assert saved.read_bytes() == b"contenido"


def test_store_converted_markdown_saves_next_to_original_and_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)
    course_id = uuid4()
    document_id = uuid4()
    original = file_storage.store_uploaded_document(
        course_name="Cálculo III",
        course_id=course_id,
        document_id=document_id,
        filename="guía.pdf",
        content=b"%PDF-original",
    )

    converted = file_storage.store_converted_markdown(
        course_name="Cálculo III",
        course_id=course_id,
        document_id=document_id,
        original_filename="guía.pdf",
        content=b"# Guia convertida",
    )
    updated = file_storage.store_converted_markdown(
        course_name="Cálculo III",
        course_id=course_id,
        document_id=document_id,
        original_filename="guía.pdf",
        content=b"# Guia actualizada",
    )

    assert converted.parent == original.parent
    assert converted.name == f"{document_id.hex}_guía.md"
    assert converted == updated
    assert original.read_bytes() == b"%PDF-original"
    assert converted.read_bytes() == b"# Guia actualizada"
