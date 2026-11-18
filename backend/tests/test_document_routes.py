import warnings
from contextlib import nullcontext

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session
from backend.storage import file_storage
from db.courses import create_course, memberships
from db.documents import DuplicateDocumentWarning

from .test_courses import _as_user, _user


def _pdf_bytes(*, with_table=False, with_image=False):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Este documento contiene suficiente texto extraible para validar el PDF.",
    )
    if with_table:
        for x in (72, 220, 360):
            page.draw_line((x, 200), (x, 280))
        for y in (200, 240, 280):
            page.draw_line((72, y), (360, y))
        page.insert_text((90, 225), "Columna A")
        page.insert_text((240, 225), "Columna B")
        page.insert_text((90, 265), "Uno")
        page.insert_text((240, 265), "Dos")
    if with_image:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5, 5), 0)
        pixmap.clear_with(255)
        page.insert_image(pymupdf.Rect(72, 300, 120, 348), pixmap=pixmap)
    data = doc.tobytes()
    doc.close()
    return data


def test_document_upload_replay_listing_and_status(postgres, monkeypatch):
    owner, outsider = _user(postgres), _user(postgres)
    owner_token = issue_session(postgres, owner)
    outsider_token = issue_session(postgres, outsider)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Documentos")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))

    client = TestClient(http_api.app)
    path = f"/api/v1/courses/{course}/documents"
    owner_headers = {"Authorization": f"Bearer {owner_token}", "Idempotency-Key": "upload"}
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    files = [("files", ("guide.md", b"contenido", "text/markdown"))]
    first = client.post(path, files=files, headers=owner_headers)
    assert first.status_code == 202
    (accepted,) = first.json()["data"]
    assert accepted["documentId"] and accepted["jobId"] and not accepted["duplicate"]
    assert client.post(path, files=files, headers=owner_headers).json() == first.json()
    changed = [("files", ("guide.md", b"distinto", "text/markdown"))]
    assert client.post(path, files=changed, headers=owner_headers).status_code == 409
    with warnings.catch_warnings():
        warnings.simplefilter("error", DuplicateDocumentWarning)
        duplicate = client.post(
            path, files=files, headers=owner_headers | {"Idempotency-Key": "other"}
        )
    assert duplicate.json()["data"] == [{"documentId": None, "jobId": None, "duplicate": True}]

    listed = client.get(path, headers=owner_headers).json()
    assert [item["id"] for item in listed["data"]] == [accepted["documentId"]]
    assert listed["nextCursor"] is None
    status_path = f"{path}/{accepted['documentId']}/status"
    assert client.get(status_path, headers=owner_headers).json()["data"]["status"] == "queued"
    assert client.get(status_path, headers=outsider_headers).status_code == 404
    assert client.get(path, headers=outsider_headers).status_code == 404


def test_document_upload_accepts_text_and_pdfs_with_tables_or_images(
    postgres, monkeypatch, tmp_path
):
    owner = _user(postgres)
    owner_token = issue_session(postgres, owner)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo III")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)

    client = TestClient(http_api.app)
    path = f"/api/v1/courses/{course}/documents"
    pdf = _pdf_bytes()
    response = client.post(
        path,
        files=[
            ("files", ("guia.md", b"# Apuntes", "text/markdown")),
            ("files", ("resumen.txt", "Apuntes de cálculo".encode(), "text/plain")),
            ("files", ("clase.pdf", pdf, "application/pdf")),
        ],
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "all-supported-formats",
        },
    )

    assert response.status_code == 202
    (course_folder,) = list(tmp_path.iterdir())
    assert course_folder.name.startswith("calculo-iii-")
    saved_files = {
        path.name.rsplit("_", 1)[-1]: path.read_bytes() for path in course_folder.iterdir()
    }
    assert saved_files == {
        "guia.md": b"# Apuntes",
        "resumen.txt": "Apuntes de cálculo".encode(),
        "clase.pdf": pdf,
    }
    for filename, pdf in (
        ("con-tabla.pdf", _pdf_bytes(with_table=True)),
        ("con-imagen.pdf", _pdf_bytes(with_image=True)),
    ):
        accepted = client.post(
            path,
            files=[("files", (filename, pdf, "application/pdf"))],
            headers={
                "Authorization": f"Bearer {owner_token}",
                "Idempotency-Key": f"allow-{filename}",
            },
        )
        assert accepted.status_code == 202
        document_id = accepted.json()["data"][0]["documentId"].replace("-", "")
        saved = course_folder / f"{document_id}_{filename}"
        assert saved.read_bytes() == pdf


def test_document_detail_download_and_delete_follow_membership(postgres, monkeypatch):
    owner, student, outsider = (_user(postgres) for _ in range(3))
    tokens = {user: issue_session(postgres, user) for user in (owner, student, outsider)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Archivos")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    _as_user(postgres, owner)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)

    def headers(user):
        return {"Authorization": f"Bearer {tokens[user]}"}

    collection = f"/api/v1/courses/{course}/documents"
    original = "Árbol\n".encode()
    uploaded = client.post(
        collection,
        files=[("files", ("guía.md", original, "text/markdown"))],
        headers=headers(owner) | {"Idempotency-Key": "details"},
    )
    assert uploaded.status_code == 202
    path = f"{collection}/{uploaded.json()['data'][0]['documentId']}"
    detail = client.get(path, headers=headers(student))
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == "guía.md"
    assert detail.json()["data"]["size"] == len(original)
    download = client.get(path + "/download", headers=headers(student))
    assert download.status_code == 200
    assert download.content == original
    assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''gu%C3%ADa.md"
    for endpoint in (path, path + "/download"):
        assert client.get(endpoint, headers=headers(outsider)).status_code == 404

    assert client.delete(path, headers=headers(outsider)).status_code == 404
    assert client.delete(path, headers=headers(student)).status_code == 403
    deleted = client.delete(path, headers=headers(owner))
    assert deleted.status_code == 204 and deleted.content == b""
    assert client.get(path, headers=headers(owner)).status_code == 404
    assert client.get(path + "/download", headers=headers(owner)).status_code == 404
    assert client.delete(path, headers=headers(owner)).status_code == 404
